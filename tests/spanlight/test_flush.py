from __future__ import annotations

import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class OTLPCollectorStub(BaseHTTPRequestHandler):
    """Minimal OTLP span collector for testing.

    Listens for POST /v1/traces and stashes the request body.
    """

    received_spans: list[dict] = []

    def do_POST(self) -> None:
        if self.path == "/v1/traces":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            # Parse the OTLP export request. It's protobuf, but for a test
            # stub we just need to confirm something arrived.
            OTLPCollectorStub.received_spans.append({"body_len": len(body)})

            # Return 200 OK.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"")
        else:
            self.send_error(404)

    def log_message(self, format, *args):  # noqa: ARG002
        # Suppress HTTP server logging.
        pass


def find_free_port() -> int:
    """Find an available port for the test server."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def test_spans_are_flushed_on_process_exit() -> None:
    """A process exiting immediately after a span still exports it.

    When a short-lived process (like a failing gate job or a CLI tool) emits
    a span and exits, the BatchSpanProcessor has not yet fired its export
    timer. Without an explicit flush, the span dies with the process. This
    test verifies that `spanlight.init()` installs a process exit handler
    that flushes any pending spans.
    """
    OTLPCollectorStub.received_spans = []
    port = find_free_port()

    # Start the collector stub in a background thread.
    server = HTTPServer(("127.0.0.1", port), OTLPCollectorStub)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Give the server time to start.
    time.sleep(0.1)

    try:
        # Run a subprocess that emits one span and exits immediately.
        script = f"""
import os
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://127.0.0.1:{port}"

import spanlight
spanlight.init("test-flush", sample_rate=1.0)

with spanlight.model_span(provider="mock"):
    pass
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == 0, (
            f"Subprocess failed: {result.stderr.decode()}"
        )

        # Give the export a moment to arrive.
        time.sleep(0.5)

        # Verify that at least one span export arrived at the collector.
        assert len(OTLPCollectorStub.received_spans) > 0, (
            "No spans received; process exit flush may not be working"
        )
    finally:
        server.shutdown()
