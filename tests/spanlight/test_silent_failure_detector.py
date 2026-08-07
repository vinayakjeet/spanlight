from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight._detector_framework import SESSION, registry
from spanlight._detectors import silent_tool_failure_detector, watch_for_silent_failure
from spanlight.attributes import DETECTION


class ToolError(Exception):
    pass


@pytest.fixture(autouse=True)
def _registered() -> None:
    registry.register(watch_for_silent_failure)
    registry.register(silent_tool_failure_detector, phase=SESSION)


def _failing_tool() -> None:
    with pytest.raises(ToolError), spanlight.tool_span("search"):
        raise ToolError("upstream index unavailable")


def _session_span(spans: InMemorySpanExporter):
    (span,) = [s for s in spans.get_finished_spans() if s.name == "session"]
    return span


def test_fires_when_the_run_carries_on_and_reports_success(
    spans: InMemorySpanExporter,
) -> None:
    """SPEC S4: a tool failed, a model ran afterwards, and the session did not
    end ERROR. That is an agent told a tool broke which answered anyway."""
    with spanlight.session():
        _failing_tool()
        with spanlight.model_span(provider="mock"):
            pass

    assert _session_span(spans).attributes[DETECTION] == "silent_tool_failure"


def test_does_not_fire_when_the_session_admits_the_failure(
    spans: InMemorySpanExporter,
) -> None:
    """The negative case. An agent that hits a broken tool, tries a model call,
    and then correctly fails is behaving well and must stay quiet."""
    with pytest.raises(ToolError), spanlight.session():
        _failing_tool()
        with spanlight.model_span(provider="mock"):
            pass
        raise ToolError("giving up")

    assert DETECTION not in _session_span(spans).attributes


def test_does_not_fire_when_no_model_ran_after_the_failure(
    spans: InMemorySpanExporter,
) -> None:
    """A tool that failed with nothing after it is a visible failure, not a
    silent one. Something has to have carried on for the silence to matter."""
    with spanlight.session():
        _failing_tool()

    assert DETECTION not in _session_span(spans).attributes


def test_does_not_fire_when_the_tool_succeeded(spans: InMemorySpanExporter) -> None:
    with spanlight.session():
        with spanlight.tool_span("search"):
            pass
        with spanlight.model_span(provider="mock"):
            pass

    assert DETECTION not in _session_span(spans).attributes


def test_a_model_call_before_the_failure_does_not_count(
    spans: InMemorySpanExporter,
) -> None:
    """Ordering is the whole rule. A model call that ran before the tool broke
    cannot have ignored it."""
    with spanlight.session():
        with spanlight.model_span(provider="mock"):
            pass
        _failing_tool()

    assert DETECTION not in _session_span(spans).attributes


def test_the_detection_lands_on_the_session_not_the_tool(
    spans: InMemorySpanExporter,
) -> None:
    """Why this needs a session span. The offending tool span closed long before
    the verdict was knowable, and an ended span cannot be marked."""
    with spanlight.session():
        _failing_tool()
        with spanlight.model_span(provider="mock"):
            pass

    flagged = [
        s.name
        for s in spans.get_finished_spans()
        if s.attributes.get(DETECTION) is not None
    ]
    assert flagged == ["session"]
