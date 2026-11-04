"""The public demo uses synthetic state and never calls the inverter API."""
import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_demo_matches_dashboard_and_blocks_network():
    spec = importlib.util.spec_from_file_location('build_demo', ROOT/'scripts/build-demo.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    html = (ROOT/'docs/index.html').read_text()
    assert module.build() == html
    assert "connect-src 'none'" in html
    assert "await fetch(" not in html
    assert 'Synthetic data' in html
    assert 'nasnas.local' not in html


def test_demo_injects_offline_transport_before_the_app():
    html = (ROOT/'docs/index.html').read_text()
    transport = html.index('window.__ENERGY_DEMO__ = {fetch: demoFetch')
    app = html.index('<script type="module"')
    assert transport < app, 'Demo transport must exist before the dashboard module runs'
    assert 'GitHub' in html and 'Interactive demo' in html
