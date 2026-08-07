from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight._detector_framework import registry
from spanlight._detectors import loop_detector
from spanlight.attributes import DETECTION


@pytest.fixture(autouse=True)
def _registered() -> None:
    registry.register(loop_detector)
    yield
    registry.clear_detectors()
    registry.reset()


def test_the_third_identical_call_is_flagged(spans: InMemorySpanExporter) -> None:
    with spanlight.session():
        for _ in range(3):
            with spanlight.tool_span("search", args={"q": "pm-kisan"}):
                pass

    first, second, third = [s for s in spans.get_finished_spans() if s.name != "session"]
    assert DETECTION not in first.attributes
    assert DETECTION not in second.attributes
    assert third.attributes[DETECTION] == "loop"


def test_arguments_differing_by_one_character_are_not_a_loop(
    spans: InMemorySpanExporter,
) -> None:
    """The negative case that keeps the detector off an agent making progress."""
    with spanlight.session():
        for query in ("pm-kisan", "pm-kisan2", "pm-kisan3"):
            with spanlight.tool_span("search", args={"q": query}):
                pass

    for span in spans.get_finished_spans():
        assert DETECTION not in span.attributes


def test_a_detection_lands_on_a_real_span_not_a_mock(
    spans: InMemorySpanExporter,
) -> None:
    """Regression: detectors used to run from a `SpanProcessor.on_end`, which is
    handed a `ReadableSpan` with no `set_attribute` at all. That raised
    `AttributeError` on the first real detection while the unit tests stayed
    green, because they passed a `MagicMock` that accepts any call."""
    with spanlight.session():
        for _ in range(3):
            with spanlight.tool_span("search", args={"q": "x"}):
                pass

    exported = spans.get_finished_spans()
    assert any(s.attributes.get(DETECTION) == "loop" for s in exported)


def test_counts_do_not_carry_between_sessions(spans: InMemorySpanExporter) -> None:
    for _ in range(3):
        with spanlight.session(), spanlight.tool_span("search", args={"q": "x"}):
            pass

    for span in spans.get_finished_spans():
        assert DETECTION not in span.attributes
