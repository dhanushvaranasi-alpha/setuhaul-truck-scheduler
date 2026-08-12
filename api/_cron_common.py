import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db  # noqa: E402
from src.clock_state import get_clock  # noqa: E402


def make_cron_handler(sweep_fn):
    """Vercel invokes cron-triggered functions with an Authorization: Bearer
    <CRON_SECRET> header — verify it before running anything (Vercel docs,
    cron-jobs/manage-cron-jobs). sweep_fn takes a Clock and returns a summary
    dict."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            expected = os.environ.get("CRON_SECRET")
            auth = self.headers.get("Authorization")
            if not expected or auth != f"Bearer {expected}":
                self.send_response(401)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
                return

            with db.get_conn() as con:
                clock = get_clock(con)
            summary = sweep_fn(clock)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(summary).encode())

    return Handler
