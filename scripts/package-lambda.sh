#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build
target="$(mktemp -d build/lambda-package.XXXXXX)"
trap 'rm -rf "$target"' EXIT
# Build a Linux arm64 ZIP without Docker/ECR. No cloud calls or secret copies.
uv pip install --target "$target" --python-version 3.12 \
  --python-platform aarch64-manylinux_2_28 --only-binary :all: \
  -r requirements-lambda.txt
cp -R src "$target/"
cp lambda_handler.py manager.yaml solar_config.yaml config.py "$target/"
printf '%s\n' 'from lambda_handler import lambda_handler' > "$target/index.py"
uv run python - "$target" <<'PY'
from pathlib import Path
import zipfile
import sys
root=Path(sys.argv[1])
files=[p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']
total=sum(p.stat().st_size for p in files)
if total>250*1024*1024:raise SystemExit('Package exceeds Lambda uncompressed ZIP size limit')
out=Path('build/household-manager.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for file in files:archive.write(file,file.relative_to(root))
print(f'Package: {out.absolute()} ({out.stat().st_size/1024**2:.1f} MiB compressed; {total/1024**2:.1f} MiB unpacked)')
if out.stat().st_size>50*1024*1024:raise SystemExit('Package exceeds direct-upload limit; do not deploy with paid staging storage.')
PY
