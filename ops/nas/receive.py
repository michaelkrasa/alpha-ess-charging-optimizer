#!/usr/bin/env python3
"""Fixed privileged entry point: receive one bounded image, then run the updater."""
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

MAX_IMAGE_BYTES = 2 * 1024 ** 3


def receive(stream, destination):
    header = stream.readline(4097)
    if len(header) > 4096 or not header.endswith(b'\n'):
        raise ValueError('Invalid deployment header')
    info = json.loads(header)
    if info == {'probe': True}:
        return info
    size = info.get('size')
    if (type(size) is not int or not 0 < size <= MAX_IMAGE_BYTES
            or not re.fullmatch(r'[0-9a-f]{64}', info.get('sha256', ''))
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', info.get('image_id', ''))):
        raise ValueError('Invalid deployment metadata')
    checksum = hashlib.sha256()
    remaining = size
    with destination.open('xb') as output:
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError('Incomplete image upload; no container changes made')
            output.write(block)
            checksum.update(block)
            remaining -= len(block)
    if checksum.hexdigest() != info['sha256']:
        raise ValueError('Image checksum mismatch; no container changes made')
    return info


def main():
    # Imported from the root-owned installation, never the upload directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from update import apply_archive
    import os
    import signal
    if len(sys.argv) != 1 or os.geteuid() != 0:
        raise RuntimeError('This helper takes no arguments and requires root')
    os.umask(0o077)
    # A stalled client must not retain a privileged process indefinitely.
    signal.alarm(900)
    with tempfile.TemporaryDirectory(prefix='alphaess-upload-') as work:
        archive = Path(work) / 'image.tar.gz'
        info = receive(sys.stdin.buffer, archive)
        signal.alarm(0)
        if info.get('probe') is True:
            print(json.dumps({'protocol': 1, 'uid': os.geteuid()}))
            return
        apply_archive(archive, info['sha256'], info['image_id'])


if __name__ == '__main__':
    main()
