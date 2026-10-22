#!/usr/bin/env python3
"""Update the commissioned NAS app; preserve its state and roll back failed images."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time

APP = Path('/volume1/docker/alphaess-household')
DOCKER = '/var/packages/ContainerManager/target/usr/bin/docker'
PROBE = """import json,urllib.request
s=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=5))
print(json.dumps({'enabled':s['control']['enabled'],'planner':s['standard_planner'],
'live':s['live'],'health':(s.get('health') or {}).get('status'),
'cycle':(s.get('health') or {}).get('timestamp'),
'contact':(s.get('device_connection') or {}).get('last_success_at'),
'connected':(s.get('device_connection') or {}).get('status')}))
"""


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(command, timeout=300):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command failed: ' + Path(command[0]).name)
    return result.stdout


def compose(app):
    command = [DOCKER, 'compose', '-p', 'alphaess-household', '--project-directory', str(app),
               '--env-file', str(app / '.env'), '-f', str(app / 'compose.nas.yaml')]
    if (app / 'nas-live.enabled').exists():
        command += ['-f', str(app / 'compose.nas.live.yaml')]
    return command


def probe(runner=run):
    return json.loads(runner([DOCKER, 'exec', 'alphaess-household', 'python', '-c', PROBE], timeout=15))


def wait_for_cycle(since, enabled, live, runner=run, pause=time.sleep, attempts=60):
    for _ in range(attempts):
        try:
            state = probe(runner)
            cycle = datetime.fromisoformat(state['cycle'])
            contact = datetime.fromisoformat(state['contact'])
            if (state['planner'] == 'emhass' and state['live'] is live
                    and state['enabled'] is enabled and state['connected'] == 'connected'
                    and state['health'] in ('active', 'observing')
                    and cycle >= since and contact >= since
                    and 0 <= (datetime.now(timezone.utc) - contact).total_seconds() <= 180):
                return state
        except (RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            pass
        pause(5)
    raise RuntimeError('No fresh successful controller cycle after the update')


def container_started(runner):
    value = runner([DOCKER, 'inspect', '--format', '{{.State.StartedAt}}', 'alphaess-household']).strip()
    return datetime.fromisoformat(re.sub(r'(\.\d{6})\d+', r'\1', value).replace('Z', '+00:00'))


def update(app, archive, expected_id, runner=run, verify=wait_for_cycle):
    before = probe(runner)
    old_id = runner([DOCKER, 'inspect', '--format', '{{.Image}}', 'alphaess-household']).strip()
    runner([DOCKER, 'tag', old_id, 'alphaess-household:rollback'])
    command = compose(app)
    try:
        runner([DOCKER, 'load', '-i', str(archive)], timeout=600)
        actual = runner([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', 'alphaess-household:local']).strip()
        if actual != expected_id:
            raise RuntimeError('Loaded image identity differs from the locally tested image')
        runner(command + ['up', '-d', '--force-recreate', '--no-build', '--pull', 'never'], timeout=120)
        state = verify(container_started(runner), before['enabled'], before['live'], runner=runner)
        return {'status': 'updated', 'image': actual, 'controller': state}
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as failure:
        runner([DOCKER, 'tag', old_id, 'alphaess-household:local'])
        runner(command + ['up', '-d', '--force-recreate', '--no-build', '--pull', 'never'], timeout=120)
        verify(container_started(runner), before['enabled'], before['live'], runner=runner)
        raise RuntimeError('Update failed; previous image restored and verified') from failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--image-id', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run through sudo')
    for path in (APP, APP / '.env', APP / 'compose.nas.yaml', APP / 'compose.nas.live.yaml'):
        stat = path.stat()
        if path.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o022:
            raise RuntimeError('Deployment ownership/permissions are unsafe')
    os.umask(0o077)
    with (APP / '.nas-update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='update-', dir=APP) as work:
            copied = Path(work) / 'image.tar.gz'
            shutil.copyfile(args.archive, copied)
            if digest(copied) != args.sha256:
                raise RuntimeError('Image archive checksum mismatch; no container changes made')
            backups = APP / 'backups'
            backups.mkdir(mode=0o700, exist_ok=True)
            target = backups / ('before-update-' + str(time.time_ns()) + '.sqlite3')
            with sqlite3.connect(str(APP / 'data/manager.sqlite3')) as source, sqlite3.connect(str(target)) as destination:
                source.backup(destination)
            target.chmod(0o600)
            result = update(APP, copied, args.image_id)
            result['checked_at'] = datetime.now(timezone.utc).isoformat()
            (APP / 'last-update.json').write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result))


if __name__ == '__main__':
    main()
