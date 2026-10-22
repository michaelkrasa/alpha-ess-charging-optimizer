#!/usr/bin/env python3
"""One-time root installation of the fixed, passwordless image-update helper."""
import argparse
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess

DEST = Path('/usr/local/libexec/alphaess-deploy')
COMMAND = Path('/usr/local/sbin/alphaess-update')
RULE = Path('/etc/sudoers.d/alphaess-update')


def rule_for(user):
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]*', user):
        raise ValueError('Invalid NAS account name')
    # sudoers empty argument string permits only a command with no arguments.
    return user + ' ALL=(root) NOPASSWD: ' + str(COMMAND) + ' ""\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    args = parser.parse_args()
    rule = rule_for(args.user)
    if os.geteuid() != 0:
        parser.error('Run this installer through sudo once')
    pwd.getpwnam(args.user)
    visudo = shutil.which('visudo') or '/usr/sbin/visudo'
    if not Path('/etc/sudoers.d').is_dir():
        raise RuntimeError('sudoers.d is unavailable; no sudo policy changed')
    if not re.search(r'^\s*(?:#|@)includedir\s+/etc/sudoers.d\s*$', Path('/etc/sudoers').read_text(), re.M):
        raise RuntimeError('sudo does not include sudoers.d; no sudo policy changed')
    for directory in (DEST, COMMAND.parent):
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_uid != 0 or directory.stat().st_mode & 0o022:
            raise RuntimeError('Unsafe installation directory')
    DEST.chmod(0o700)
    for name in ('update.py', 'receive.py'):
        target = DEST / name
        if target.is_symlink():
            raise RuntimeError('Unsafe installation file')
        shutil.copyfile(Path(__file__).resolve().parent / name, target)
        os.chown(target, 0, 0)
        target.chmod(0o600)
    if COMMAND.is_symlink() or RULE.is_symlink():
        raise RuntimeError('Unsafe helper or policy file')
    COMMAND.write_text('#!/bin/sh\nexec /usr/bin/python3 -I ' + str(DEST / 'receive.py') + '\n')
    os.chown(COMMAND, 0, 0)
    COMMAND.chmod(0o755)
    candidate = RULE.with_suffix('.candidate')
    candidate.write_text(rule)
    os.chown(candidate, 0, 0)
    candidate.chmod(0o440)
    try:
        subprocess.run([visudo, '-cf', str(candidate)], check=True)
        candidate.replace(RULE)
    finally:
        candidate.unlink(missing_ok=True)
    print('Installed fixed AlphaESS updater. Only this no-argument command is passwordless.')


if __name__ == '__main__':
    main()
