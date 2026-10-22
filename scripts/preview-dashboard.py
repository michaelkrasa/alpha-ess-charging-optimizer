#!/usr/bin/env python3
"""Preview local dashboard changes with read-only telemetry from the NAS."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.manager.energy_day import cache_daily_totals, cache_power_history, day_measurements
from src.manager.plan_insight import explain_plan
from src.manager.soc_tracking import dashboard_soc


def enrich_state(state, power_history=(), daily_energy=None):
    """Preview new read-only views without inventing past expected SOC."""
    now = datetime.fromisoformat(state['now'])
    energy = dict(state.get('energy_day') or {})
    class HistoryCache:
        def __init__(self):
            self.value = None
        def get(self, key):
            return self.value
        def put(self, key, value):
            self.value = value
    cache = HistoryCache()
    cache_power_history(cache, power_history, now)
    energy['rows'] = day_measurements([
        *cache.value['rows'],
        *energy.get('rows', []),
        *(r['telemetry'] for r in state.get('history', []) if r.get('telemetry')),
        *([state['telemetry']] if state.get('telemetry') else []),
    ], now)
    class EmptyStore:
        def get(self, key):
            return None
    state['energy_day'] = energy
    if daily_energy:
        totals = HistoryCache()
        try:
            cache_daily_totals(totals, daily_energy, now)
            totals.value['checked_at'] = daily_energy['preview_captured_at']
            state['daily_energy'] = totals.value
        except ValueError:
            pass
    if 'soc_tracking' not in state:
        state['soc_tracking'] = dashboard_soc(EmptyStore(), now, energy)
    state['plan_insights'] = explain_plan(state.get('plan'), state.get('price_intervals', []))
    return state


def make_handler(source, power_history=(), daily_energy=None):
    class Preview(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body = (ROOT / "src/manager/dashboard.html").read_bytes()
                content = "text/html; charset=utf-8"
            elif self.path == "/api/state":
                try:
                    with urllib.request.urlopen(source + "/api/state", timeout=8) as response:
                        body = json.dumps(enrich_state(json.load(response), power_history, daily_energy)).encode()
                    content = "application/json"
                except (OSError, urllib.error.URLError):
                    self.send_error(502, "NAS telemetry is unavailable")
                    return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            body = b'{"detail":"Read-only preview. Controls remain on the NAS."}'
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    return Preview


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="http://nasnas.local:8765")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--power-history", type=Path, help="Optional private AlphaESS day-power JSON snapshot for measured SOC")
    parser.add_argument("--daily-energy", type=Path, help="Optional private AlphaESS daily-energy JSON snapshot")
    args = parser.parse_args()
    print(f"Read-only local preview: http://127.0.0.1:{args.port}", flush=True)
    print("Local HTML + live NAS readings; POST requests are blocked.", flush=True)
    samples = json.loads(args.power_history.read_text()) if args.power_history else ()
    totals = json.loads(args.daily_energy.read_text()) if args.daily_energy else None
    if totals is not None:
        totals['preview_captured_at'] = datetime.fromtimestamp(args.daily_energy.stat().st_mtime, timezone.utc).isoformat()
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.source.rstrip("/"), samples, totals)).serve_forever()
