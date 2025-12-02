#!/bin/bash
# Build standalone executable for ESS Optimizer

set -e

echo "🔧 Building ESS Optimizer standalone executable..."

# Ensure we're in the right directory
cd "$(dirname "$0")"

# Install/update dependencies
echo "📦 Installing dependencies..."
uv sync --extra dev

# Build executable
echo "🏗️  Building executable..."
uv run pyinstaller --onefile --name ess-optimizer --clean \
  --hidden-import=voluptuous \
  --hidden-import=voluptuous.error \
  --hidden-import=voluptuous.validators \
  --hidden-import=voluptuous.schema_builder \
  --hidden-import=aiohttp \
  --hidden-import=certifi \
  --collect-all=alphaess \
  --collect-all=ote_cr_price_fetcher \
  --collect-all=voluptuous \
  ESS.py

# Show result
echo ""
echo "✅ Build complete!"
echo ""
ls -lh dist/ess-optimizer
echo ""
echo "📍 Executable location: dist/ess-optimizer"
echo ""
echo "Usage:"
echo "  ./dist/ess-optimizer           # Run continuous monitoring (default)"
echo "  ./dist/ess-optimizer --once    # Run single optimization"
echo ""
echo "To distribute, copy:"
echo "  - dist/ess-optimizer (the executable)"
echo "  - config.yaml (your configuration)"

