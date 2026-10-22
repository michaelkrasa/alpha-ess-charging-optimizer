#!/usr/bin/env python3
"""Preview local dashboard changes with read-only telemetry from the NAS."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def make_handler(source):
    class Preview(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body = (ROOT / "src/manager/dashboard.html").read_bytes()
                content = "text/html; charset=utf-8"
            elif self.path == "/api/state":
                try:
                    with urllib.request.urlopen(source + "/api/state", timeout=8) as response:
                        body = response.read()
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
    args = parser.parse_args()
    print(f"Read-only local preview: http://127.0.0.1:{args.port}", flush=True)
    print("Local HTML + live NAS readings; POST requests are blocked.", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.source.rstrip("/"))).serve_forever()
