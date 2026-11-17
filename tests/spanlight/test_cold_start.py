from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
import spanlight._spans as spans_module
from spanlight.attributes import COLD_START


def test_only_the_first_recording_span_is_marked(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract and the dashboards promise this attribute. Until this test
    existed nothing emitted it, and the contract test could not notice, because it
    compares two lists of names rather than what a span carries."""
    monkeypatch.setattr(spans_module, "_cold_start_pending", True)

    with spanlight.session(), spanlight.tool_span("lookup"):
        pass
    with spanlight.session():
        pass

    tool, first_session, second_session = spans.get_finished_spans()
    assert first_session.attributes[COLD_START] is True
    assert COLD_START not in tool.attributes
    assert COLD_START not in second_session.attributes


def test_a_span_nobody_records_does_not_use_up_the_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instrumented code can run before init(). If a no-op span consumed the mark,
    the real first span after spin-up would arrive unmarked."""
    monkeypatch.setattr(spans_module, "_cold_start_pending", True)
    monkeypatch.setattr(spans_module, "get_tracer", lambda: trace.NoOpTracer())

    with spanlight.session():
        pass

    assert spans_module._cold_start_pending is True
