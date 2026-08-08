"""Fault injection for tests that need a dependency to misbehave.

Every fault is served over HTTP, because both things Spanlight talks to are HTTP:
the OTLP exporter and the chassis LLM providers. One server can therefore stand
in for either, and a test picks the failure rather than the mechanism.

Built here because Spanlight needs it first. It belongs in the chassis so the
other ten projects reuse it, and it is deliberately free of anything
Spanlight-specific so that move is a copy rather than a rewrite.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Fault(Enum):
    """How the far side fails.

    These are the shapes that actually show up in QUOTAS.md and in ShipGate's
    logs, not a taxonomy invented for completeness. `HANG` is the one worth
    naming: a dependency that is slow is far more dangerous than one that is
    down, because nothing reports an error and the caller simply stops.
    """

    UNREACHABLE = "unreachable"
    SERVER_ERROR = "server_error"
    HANG = "hang"
    RESET = "reset"
    RATE_LIMITED = "rate_limited"
    MALFORMED = "malformed"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
        except Exception:
            return

        self.server.requests.append(self.path)
        fault = self.server.fault

        if fault is Fault.HANG:
            # Outlives any sane client timeout without pinning the thread
            # forever, so a test that forgets to set one still finishes.
            time.sleep(self.server.hang_seconds)
            return
        if fault is Fault.RESET:
            self.close_connection = True
            self.wfile.close()
            return
        if fault is Fault.SERVER_ERROR:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"upstream is having a day")
            return
        if fault is Fault.RATE_LIMITED:
            self.send_response(429)
            self.send_header("Retry-After", str(self.server.retry_after))
            self.end_headers()
            self.wfile.write(b'{"error": "rate limit exceeded"}')
            return
        if fault is Fault.MALFORMED:
            # A 200 carrying nonsense. Worse than an error status, because every
            # status check passes and the failure surfaces at parse time.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{not json at all")
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"")

    def log_message(self, *args: object) -> None:
        pass


class _Server(ThreadingHTTPServer):
    """Threaded, with daemon threads, specifically because of `HANG`.

    A single-threaded server handles one request at a time in the accept loop, so
    a handler sleeping out a hang blocks `shutdown()` for the full duration. The
    suite then pays the hang twice: once for the client timeout it is testing,
    and again waiting to tear the server down.
    """

    daemon_threads = True

    fault: Fault
    requests: list[str]
    hang_seconds: float
    retry_after: int


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def faulty_endpoint(
    fault: Fault, hang_seconds: float = 30.0, retry_after: int = 40
) -> Iterator[_Server]:
    """Serve `fault` at a base URL, yielding the server so a test can count hits.

    `UNREACHABLE` binds nothing and hands back a port with no listener, which is
    a connection refused rather than a simulated one.

    `retry_after` defaults to 40 seconds because that is what a real Gemini 429
    asked for, recorded in QUOTAS.md. Five attempts of exponential backoff total
    about 31, so a client that ignores the header retries entirely inside the
    cooldown and fails with quota to spare.
    """
    port = _free_port()

    if fault is Fault.UNREACHABLE:
        server = _Server.__new__(_Server)
        server.requests = []
        server.url = f"http://127.0.0.1:{port}"
        yield server
        return

    server = _Server(("127.0.0.1", port), _Handler)
    server.fault = fault
    server.requests = []
    server.hang_seconds = hang_seconds
    server.retry_after = retry_after
    server.url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
