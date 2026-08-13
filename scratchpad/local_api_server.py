"""Local stand-in for Vercel's Python runtime.

On Vercel, every file under api/ that exports a BaseHTTPRequestHandler
subclass named `handler` becomes its own serverless function, routed by
its file path (api/dashboard/docks.py -> /api/dashboard/docks). Locally
nothing provides that routing, so web/next.config.ts proxies /api/* here
(NODE_ENV=development only) and this process replicates the same
path -> module mapping by hand.

Run with: uv run --env-file .env scratchpad/local_api_server.py
"""

import importlib
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORT = 8000


def _resolve(path: str):
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] != "api":
        return None
    module_name = "api." + ".".join(parts[1:])
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None
    return getattr(module, "handler", None)


class Dispatcher(BaseHTTPRequestHandler):
    def _dispatch(self):
        path = urlparse(self.path).path
        target = _resolve(path)
        if target is None:
            self._json_error(404, "not found")
            return

        method_name = "do_GET" if self.command == "GET" else "do_POST"
        # Reassigning __class__ hands this already-constructed instance
        # (rfile/wfile/headers all present) to the target handler, so its
        # do_GET/do_POST run exactly as they would under Vercel.
        self.__class__ = target
        method = getattr(self, method_name, None)
        if method is None:
            self._json_error(405, "method not allowed")
            return
        method()

    def _json_error(self, status: int, message: str) -> None:
        body = f'{{"error": "{message}"}}'.encode()
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def log_message(self, format, *args):  # noqa: A002 — stdlib signature
        sys.stderr.write(f"[local_api_server] {self.address_string()} {format % args}\n")


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    # Vercel runs each api/*.py invocation as an independent, concurrent
    # function; a plain single-threaded HTTPServer here serializes every
    # request behind whichever one is slowest (e.g. an in-flight /api/chat
    # LLM call), which the real deployment never does and which makes local
    # multi-request testing (dashboard polling alongside a chat send)
    # misleading.
    daemon_threads = True


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Dispatcher)
    print(f"Local API dispatcher listening on http://127.0.0.1:{PORT} (proxies api/*.py)")
    server.serve_forever()
