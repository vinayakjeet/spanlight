from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
import spanlight._spans as spans_module
from spanlight.attributes import SESSION_ID


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(spans_module, "get_tracer", lambda: provider.get_tracer("test"))
    return exporter


def test_one_trace_spans_the_caller_and_the_route(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    """SPEC S7, HTTP half. A caller already in a session hits /agent/run, and
    the run it produces belongs to the caller's trace rather than starting a
    fresh one that nothing links back."""
    with spanlight.session("upstream-run") as upstream_id:
        carrier = spanlight.headers()

    # Sent after the caller's context has been torn down. TestClient runs the
    # app in this same process, so leaving the session open would let the route
    # inherit the context ambiently and the trace ids would match even if the
    # headers were ignored entirely.
    response = client.post("/agent/run", json={"prompt": "hello"}, headers=carrier)

    assert response.status_code == 200
    assert response.json()["session_id"] == upstream_id

    finished = exporter.get_finished_spans()
    assert len({span.context.trace_id for span in finished}) == 1
    assert {span.attributes[SESSION_ID] for span in finished} == {"upstream-run"}


def test_a_plain_request_starts_its_own_session(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    """A visitor with a browser sends no traceparent, which must be an ordinary
    new run rather than an error or an orphan."""
    first = client.post("/agent/run", json={"prompt": "hello"}).json()["session_id"]
    second = client.post("/agent/run", json={"prompt": "hello"}).json()["session_id"]

    assert first != second
    traces = {span.context.trace_id for span in exporter.get_finished_spans()}
    assert len(traces) == 2


def test_a_forged_traceparent_does_not_break_the_route(client: TestClient) -> None:
    """Inbound headers come from whoever calls the URL. Instrumentation that
    raises on a malformed one turns a bad header into an outage."""
    response = client.post(
        "/agent/run",
        json={"prompt": "hello"},
        headers={"traceparent": "garbage", "baggage": "also=garbage"},
    )

    assert response.status_code == 200
