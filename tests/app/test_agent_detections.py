from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight._spans as spans_module
from spanlight._detector_framework import SESSION, registry
from spanlight._detectors import silent_tool_failure_detector, watch_for_silent_failure
from spanlight.attributes import DETECTION, ERROR_TYPE


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(spans_module, "get_tracer", lambda: provider.get_tracer("test"))
    registry.register(watch_for_silent_failure)
    registry.register(silent_tool_failure_detector, phase=SESSION)
    yield exporter
    registry.clear_detectors()
    registry.reset()


def test_a_swallowed_tool_failure_is_caught_end_to_end(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    """The whole point, through the public HTTP surface. The route answers 200
    with a normal-looking reply while the run it describes was told a tool
    failed and never said so."""
    response = client.post("/agent/run", json={"prompt": "fail-tool please"})
    assert response.status_code == 200

    by_name = {span.name: span for span in exporter.get_finished_spans()}
    assert by_name["tool lookup_scheme"].attributes[ERROR_TYPE] == "SchemeIndexUnavailable"
    assert by_name["session"].attributes[DETECTION] == "silent_tool_failure"


def test_a_healthy_request_is_not_flagged(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    client.post("/agent/run", json={"prompt": "hello"})

    for span in exporter.get_finished_spans():
        assert DETECTION not in span.attributes
