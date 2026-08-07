from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import spanlight
from spanlight.attributes import ERROR_TYPE, TOOL_ARGS_FINGERPRINT, TOOL_NAME


def test_records_the_tool_name(spans: InMemorySpanExporter) -> None:
    with spanlight.tool_span("search_schemes"):
        pass

    (span,) = spans.get_finished_spans()
    assert span.attributes[TOOL_NAME] == "search_schemes"
    assert TOOL_ARGS_FINGERPRINT not in span.attributes


def test_arguments_are_fingerprinted_never_recorded(spans: InMemorySpanExporter) -> None:
    with spanlight.tool_span("lookup", args={"aadhaar": "1234-5678-9012"}):
        pass

    (span,) = spans.get_finished_spans()
    assert span.attributes[TOOL_ARGS_FINGERPRINT]
    assert "1234-5678-9012" not in str(span.attributes)


def test_identical_arguments_fingerprint_identically() -> None:
    """What M3.2 loop detection rests on: same tool, same args, N times."""
    assert spanlight.fingerprint({"q": "pm-kisan"}) == spanlight.fingerprint({"q": "pm-kisan"})


def test_argument_order_does_not_change_the_fingerprint() -> None:
    assert spanlight.fingerprint({"a": 1, "b": 2}) == spanlight.fingerprint({"b": 2, "a": 1})


def test_different_arguments_fingerprint_differently() -> None:
    """The negative case for the loop detector. A fingerprint that collided on
    near-identical inputs would report every varied retry as a loop."""
    assert spanlight.fingerprint({"q": "pm-kisan"}) != spanlight.fingerprint({"q": "pm-kisam"})


def test_unserializable_arguments_do_not_raise() -> None:
    """Instrumentation that raises on an exotic argument takes down the host it
    was supposed to be watching."""
    assert spanlight.fingerprint({"conn": object()})


def test_the_decorator_fingerprints_call_arguments(spans: InMemorySpanExporter) -> None:
    @spanlight.tool("search_schemes")
    def search(query: str) -> str:
        return "results"

    search("pm-kisan")
    search("pm-kisan")
    search("ayushman")

    fingerprints = [s.attributes[TOOL_ARGS_FINGERPRINT] for s in spans.get_finished_spans()]
    assert fingerprints[0] == fingerprints[1]
    assert fingerprints[2] != fingerprints[0]


async def test_the_decorator_handles_async_tools(spans: InMemorySpanExporter) -> None:
    @spanlight.tool("fetch_page")
    async def fetch(page: int) -> int:
        return page

    assert await fetch(3) == 3
    assert spans.get_finished_spans()[0].attributes[TOOL_NAME] == "fetch_page"


def test_a_failing_tool_records_the_error_class(spans: InMemorySpanExporter) -> None:
    """Tool spans inherit the error contract from the shared helper, so this
    cannot pass for model spans while silently failing for tools."""

    class ToolTimeout(Exception):
        pass

    with pytest.raises(ToolTimeout), spanlight.tool_span("search_schemes"):
        raise ToolTimeout("upstream took too long")

    (span,) = spans.get_finished_spans()
    assert span.attributes[ERROR_TYPE] == "ToolTimeout"
    assert span.status.status_code is StatusCode.ERROR
