#!/usr/bin/env python3
"""Test and build locally, then deploy over temporary key-authenticated SSH."""
import argparse
import gzip
import hashlib
import ipaddress
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'alphaess-household:local'


def run(command, **options):
    return subprocess.run(command, check=True, cwd=ROOT, **options)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def exported_image_id(archive):
    # Docker Desktop may expose an index ID; the NAS uses the image config ID.
    import json
    with tarfile.open(archive, 'r:gz') as saved:
        manifest = json.load(saved.extractfile('manifest.json'))
        selected = next(m for m in manifest if IMAGE in (m.get('RepoTags') or []))
        return 'sha256:' + hashlib.sha256(saved.extractfile(selected['Config']).read()).hexdigest()


def check_image():
    name = 'alphaess-check-' + str(time.time_ns())
    run(['docker', 'run', '-d', '--name', name, '--network', 'none', '--read-only',
         '--tmpfs', '/tmp:mode=1777,size=128m',
         '--tmpfs', '/app/data:uid=10001,gid=10001,mode=0700,size=32m', IMAGE], stdout=subprocess.DEVNULL)
    try:
        expected = digest(ROOT / 'src/manager/dashboard.html')
        code = "import urllib.request,hashlib,emhass,highspy; assert hashlib.sha256(urllib.request.urlopen('http://127.0.0.1:8765/',timeout=3).read()).hexdigest() == " + repr(expected)
        for _ in range(30):
            checked = subprocess.run(['docker', 'exec', name, 'python', '-c', code], capture_output=True)
            if checked.returncode == 0:
                run(['docker', 'exec', name, 'python', '-c',
                     (ROOT / 'scripts/check-container-planners.py').read_text()])
                return
            time.sleep(1)
        raise RuntimeError('The isolated image did not serve the tested dashboard')
    finally:
        run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL)


def setup_helper(ssh, host, key):
    result = run(ssh + [host, 'umask 077; mktemp -d /volume1/@tmp/alphaess-setup-XXXXXXXX'], capture_output=True, text=True)
    stage = result.stdout.strip()
    if not stage.startswith('/volume1/@tmp/alphaess-setup-') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-@_' for c in stage):
        raise RuntimeError('Unexpected NAS setup directory')
    user = run(ssh + [host, 'id -un'], capture_output=True, text=True).stdout.strip()
    files = [ROOT / 'ops/nas' / name for name in ('update.py', 'receive.py', 'install_deploy.py')]
    try:
        run(['scp', '-O', '-q', *ssh[1:],
             *(str(p) for p in files), host + ':' + stage + '/'])
        lines = ''.join(digest(p) + '  ' + stage + '/' + p.name + '\n' for p in files)
        inner = 'printf %s ' + shlex.quote(lines) + ' | sha256sum -c - && exec /usr/bin/python3 -I '
        inner += shlex.quote(stage + '/install_deploy.py') + ' --user ' + shlex.quote(user)
        print('One-time authorization: enter your NAS password. Future updates will not prompt.', flush=True)
        run(ssh + ['-t', host, 'sudo /bin/sh -c ' + shlex.quote(inner)])
        probe_helper(ssh, host)
        print('Setup complete. Keep SSH restricted to the trusted deployment computer on the LAN.', flush=True)
    finally:
        paths = ' '.join(shlex.quote(stage + '/' + p.name) for p in files)
        subprocess.run(ssh + [host, 'rm -f ' + paths + '; rmdir ' + shlex.quote(stage)], capture_output=True)


def send_image(ssh, host, archive):
    import json
    header = json.dumps({'size': archive.stat().st_size, 'sha256': digest(archive),
                         'image_id': exported_image_id(archive)}).encode() + b'\n'
    # No remote paths, uploaded scripts or shell expressions are accepted by the helper.
    process = subprocess.Popen(ssh + [host, 'sudo -n /usr/local/sbin/alphaess-update'], stdin=subprocess.PIPE, cwd=ROOT)
    try:
        process.stdin.write(header)
        with archive.open('rb') as source:
            shutil.copyfileobj(source, process.stdin)
        process.stdin.close()
    except BaseException:
        process.stdin.close()
        process.wait()
        raise
    if process.wait():
        raise RuntimeError('NAS deployment failed; review the updater output above')


def probe_helper(ssh, host):
    import json
    result = run(ssh + [host, 'sudo -n /usr/local/sbin/alphaess-update'],
                 input='{"probe":true}\n', capture_output=True, text=True)
    if json.loads(result.stdout) != {'protocol': 1, 'uid': 0}:
        raise RuntimeError('NAS updater did not pass its harmless privileged probe')


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default=os.environ.get('NAS_SSH_HOST'))
    parser.add_argument('--key', type=Path, default=Path.home() / '.ssh/id_ed25519')
    parser.add_argument('--bind-address', type=ipaddress.ip_address, default=os.environ.get('NAS_SSH_BIND_ADDRESS'),
                        help='Optional local source IP allowed by the NAS firewall')
    parser.add_argument('--image-only', action='store_true', help='Build a tested image for GUI import; no SSH needed')
    parser.add_argument('--setup', action='store_true', help='One-time installation of the restricted passwordless updater')
    args = parser.parse_args()
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=5', '-i', str(args.key)]
    if args.bind_address:
        ssh += ['-4' if args.bind_address.version == 4 else '-6', '-o', 'BindAddress=' + str(args.bind_address)]
    if not args.image_only and not args.host:
        parser.error('Supply --host USER@NAS, or NAS_SSH_HOST in your private .env')
    if args.setup:
        if args.image_only:
            parser.error('--setup and --image-only are separate operations')
        setup_helper(ssh, args.host, args.key)
        return
    if not args.image_only:
        try:
            probe_helper(ssh, args.host)
        except (subprocess.CalledProcessError, ValueError):
            raise SystemExit('NAS updater unavailable. Enable LAN-only SSH and run ./scripts/deploy-nas.sh --setup once.')
    try:
        import json
        with urllib.request.urlopen('http://127.0.0.1:8765/api/state', timeout=2) as response:
            local = json.load(response)
        if local.get('live') and local.get('control', {}).get('enabled'):
            raise SystemExit('Stop the active Mac controller first; the NAS must be the only writer.')
    except (OSError, ValueError):
        pass
    run([sys.executable, '-m', 'pytest', 'tests/', '-q'])
    revision = run(['git', 'rev-parse', '--short=12', 'HEAD'], capture_output=True, text=True).stdout.strip()
    if run(['git', 'status', '--porcelain', '--untracked-files=normal'], capture_output=True, text=True).stdout.strip():
        revision += '-dirty'
    run(['docker', 'build', '--build-arg', 'APP_VERSION=' + revision, '--platform', 'linux/arm64', '-f', 'Dockerfile.nas', '-t', IMAGE, '.'])
    check_image()
    destination = ROOT / 'dist/nas'
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / 'alphaess.tar.gz'
    with gzip.open(archive, 'wb', compresslevel=1) as output:
        saving = subprocess.Popen(['docker', 'save', IMAGE], stdout=subprocess.PIPE)
        try:
            shutil.copyfileobj(saving.stdout, output)
        finally:
            saving.stdout.close()
        if saving.wait():
            raise RuntimeError('Image export failed')
    print('Tested ARM64 image: ' + str(archive), flush=True)
    if args.image_only:
        return
    send_image(ssh, args.host, archive)
    print('Deployment verified — open port 8765 on your NAS.', flush=True)


if __name__ == '__main__':
    main()
