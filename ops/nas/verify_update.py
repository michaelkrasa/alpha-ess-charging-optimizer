#!/usr/bin/env python3
"""Benchmark a private long-horizon fixture on the NAS before replacing its service."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from update import APP, DOCKER, digest, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--disable-ssh', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run through sudo')
    os.umask(0o077)
    report_path = Path(__file__).parent / 'solver-report.json'
    old_id = run([DOCKER, 'inspect', '--format', '{{.Image}}', 'alphaess-household']).strip()
    with tempfile.TemporaryDirectory(prefix='solver-check-', dir=APP) as work:
        archive = Path(work) / 'image.tar.gz'
        shutil.copyfile(args.archive, archive)
        if digest(archive) != args.sha256:
            raise RuntimeError('Image checksum mismatch')
        fixture = Path(work) / 'input.json'
        shutil.copyfile(args.input, fixture)
        fixture.chmod(0o444)  # Read-only bind mount; parent directory stays root-private.
        try:
            run([DOCKER, 'load', '-i', str(archive)], timeout=600)
            actual = run([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', 'alphaess-household:local']).strip()
            if actual != args.image_id:
                raise RuntimeError('Image identity mismatch')
            command = [DOCKER, 'run', '--rm', '--network', 'none', '--read-only',
                       '--memory', '1g', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                       '--tmpfs', '/tmp:mode=1777,size=128m',
                       '--tmpfs', '/app/data:uid=10001,gid=10001,mode=0700,size=32m',
                       '--mount', 'type=bind,source=' + str(fixture) + ',target=/app/solver-input.json,readonly',
                       '--mount', 'type=bind,source=' + str(APP/'manager.yaml') + ',target=/app/manager.yaml,readonly',
                       args.image_id, 'python', '-m', 'src.manager.solver_check',
                       '--input', '/app/solver-input.json', '--soc', '60.8', '61.6', '62.4']
            report = json.loads(run(command, timeout=540))
            if report.get('status') != 'passed' or report.get('intervals', 0) <= 96:
                raise RuntimeError('Full-horizon solver check did not pass')
            report_path.write_text(json.dumps(report, indent=2) + '\n')
            report_path.chmod(0o644)
            print(json.dumps({'nas_full_horizon': report}), flush=True)
        except Exception:
            run([DOCKER, 'tag', old_id, 'alphaess-household:local'])
            raise
        subprocess.run([sys.executable, str(Path(__file__).with_name('update.py')),
                        '--archive', str(archive), '--sha256', args.sha256,
                        '--image-id', args.image_id], check=True)
    # A fresh controller cycle must use all currently published future slots.
    probe = """import json,urllib.request
s=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=10))
print(json.dumps({'health':s['health']['status'],'solver':s['planner_comparison']['emhass_solver'],
'accepted':s['actuation']['status']=='accepted','readback':s['actuation'].get('schedule_verified'),
'enabled':s['control']['enabled'],'live':s['live'],'connected':s['device_connection']['status']}))
"""
    state = json.loads(run([DOCKER, 'exec', 'alphaess-household', 'python', '-c', probe]))
    certificate = state.get('solver') or {}
    if not (state['health'] == 'active' and state['connected'] == 'connected'
            and state['enabled'] and state['live'] and state['accepted'] and state['readback']
            and certificate.get('intervals', 0) > 96 and certificate.get('status') == 'Optimal'
            and certificate.get('mip_gap', 1) <= 0.001 + 1e-8):
        raise RuntimeError('Live full-horizon control/readback verification failed')
    target = Path(__file__).parent / 'live-verification.json'
    target.write_text(json.dumps(state, indent=2) + '\n')
    target.chmod(0o644)
    print(json.dumps({'live_verification': state}), flush=True)
    if args.disable_ssh:
        print('Deployment verified. Closing temporary SSH in 90 seconds.', flush=True)
        time.sleep(90)
        # Run last: the SSH connection may close when the service stops.
        subprocess.run(['/usr/syno/bin/synosystemctl', 'disable', 'sshd'], check=True)
        print('Temporary SSH disabled; stopping the service now.', flush=True)
        subprocess.run(['/usr/syno/bin/synosystemctl', 'stop', 'sshd'], check=True)


if __name__ == '__main__':
    main()
