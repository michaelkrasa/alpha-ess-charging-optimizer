#!/usr/bin/env bash
set -euo pipefail
# Deployment is deliberately separate from local verification. This command only
# packages Linux wheels; it cannot overwrite an existing Lambda or its secrets.
exec bash "$(dirname "$0")/scripts/package-lambda.sh"
