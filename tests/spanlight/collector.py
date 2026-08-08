from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.paths.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"")

    def log_message(self, *args: object) -> None:
        pass


class _Collector(HTTPServer):
    paths: list[str]


@contextmanager
def collector():
    """A stand-in OTLP endpoint that records the paths it was posted to.

    Real enough for the two things worth asserting from outside the process:
    that an export happened at all, and how many. Docker is not installed on the
    build machine (SPEC A8), so a real collector container is not an option, and
    for counting requests it would not add anything.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = _Collector(("127.0.0.1", port), _Handler)
    server.paths = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
