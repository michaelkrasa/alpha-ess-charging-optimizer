"""The served dashboard is the committed build of dashboard/ and stays CSP-safe.

Behavioural checks (status derivation, schedule decoding, chart series, the
interval readout, day controls, ON/OFF and heartbeat recovery) live in the
frontend's Vitest suite, run here whenever its dependencies are installed.
"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "dashboard"
SERVED = ROOT / "src/manager/dashboard.html"


def test_served_dashboard_is_one_self_contained_file():
    html = SERVED.read_text()
    # CSP allows only same-origin and inline scripts/styles: no CDN or asset routes.
    assert not re.search(r'<script[^>]+src=', html)
    assert not re.search(r'<link[^>]+rel="stylesheet"', html)
    assert 'rel="icon"' in html and 'href="data:image/svg+xml' in html
    assert "/api/state" in html and "/api/control" in html
    assert "X-Control-Request" in html


def _frontend_ready():
    if not shutil.which("npx") or not (FRONTEND / "node_modules").exists():
        pytest.skip("Run `npm ci` in dashboard/ to include frontend checks")


def test_frontend_suite_passes():
    _frontend_ready()
    result = subprocess.run(["npx", "vitest", "run"], cwd=FRONTEND, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]


def test_committed_dashboard_matches_frontend_source(tmp_path):
    _frontend_ready()
    result = subprocess.run(
        ["npx", "vite", "build", "--outDir", str(tmp_path), "--logLevel", "error"],
        cwd=FRONTEND, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "dashboard.html").read_text() == SERVED.read_text(), (
        "src/manager/dashboard.html is stale: run `npm run build` in dashboard/ "
        "and `python scripts/build-demo.py`"
    )
