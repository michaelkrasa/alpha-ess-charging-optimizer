#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --frozen --extra emhass --extra dev python scripts/deploy_nas.py "$@"
