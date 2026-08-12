import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload) -> None:
    body = json.dumps(payload, default=str).encode()
    handler.send_response(status)
    handler.send_header("Content-type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def json_get_handler(fn):
    """fn(query: dict[str, str]) -> dict. Query values are single strings
    (first value wins for repeated params)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            try:
                result = fn(query)
                _send_json(self, 200, result)
            except LookupError as e:
                _send_json(self, 400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 — surface to the client, this is a JSON API boundary
                _send_json(self, 500, {"error": str(e)})

    return Handler


def json_post_handler(fn):
    """fn(body: dict) -> dict."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
                result = fn(body)
                _send_json(self, 200, result)
            except LookupError as e:
                _send_json(self, 400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 — surface to the client, this is a JSON API boundary
                _send_json(self, 500, {"error": str(e)})

    return Handler
