from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
import spanlight._metrics as metrics_module
from spanlight._detector_framework import SESSION, registry
from spanlight._detectors import (
    cost_ceiling_detector,
    loop_detector,
    silent_tool_failure_detector,
    watch_for_silent_failure,
)
from spanlight.attributes import DETECTIONS_TOTAL

# One improbable string, planted everywhere a caller could put user data, then
# looked for in everything that leaves the process. An allowlist of attributes
# known to be safe only catches the leaks that were predicted; a canary catches
# the ones that were not, which is the category that matters.
CANARY = "zqxjvw-canary-8f3a1c-do-not-export"


class CanaryError(Exception):
    """Its message is the canary, because `error.type` is the attribute most
    likely to be widened to include a message by someone trying to be helpful."""

    def __init__(self) -> None:
        super().__init__(f"failed while handling {CANARY}")


def _leaked(haystack: object) -> bool:
    return CANARY in str(haystack)


def _sweep(exporter: InMemorySpanExporter) -> list[str]:
    """Everything a span carries off the machine: name, attributes, status, and
    every event with its own attributes."""
    found = []
    for span in exporter.get_finished_spans():
        if _leaked(span.name):
            found.append(f"{span.name}: span name")
        for key, value in (span.attributes or {}).items():
            if _leaked(key) or _leaked(value):
                found.append(f"{span.name}: attribute {key}={value!r}")
        if _leaked(span.status.description):
            found.append(f"{span.name}: status description")
        for event in span.events:
            if _leaked(event.name):
                found.append(f"{span.name}: event name {event.name}")
            for key, value in (event.attributes or {}).items():
                if _leaked(key) or _leaked(value):
                    found.append(f"{span.name}: event attribute {key}={value!r}")
        if _leaked(span.resource.attributes):
            found.append(f"{span.name}: resource")
    return found


def _exercise_everything() -> None:
    """Plant the canary at every point a caller hands Spanlight a string."""
    registry.register(loop_detector)
    registry.register(watch_for_silent_failure)
    registry.register(cost_ceiling_detector(0.0000001))
    registry.register(silent_tool_failure_detector, phase=SESSION)

    with spanlight.session():
        # Tool arguments, three times over, so the loop detector fires and its
        # event is swept too.
        for _ in range(3):
            with spanlight.tool_span("search", args={"query": CANARY, "user": CANARY}):
                pass

        # A benign index name. `spanlight.retrieval.index` and
        # `spanlight.tool.name` are recorded verbatim by design: they are
        # deployment identifiers a developer chooses, like a table name, and
        # `test_identifiers_are_recorded_verbatim_by_design` below pins that.
        # A caller who names an index after user data has exported user data,
        # and no amount of care in this library can prevent it.
        with spanlight.retrieval_span("schemes-v3", k=3):
            pass

        # An exception whose message carries it, inside a tool, so the silent
        # failure detector also fires.
        with pytest.raises(CanaryError), spanlight.tool_span("fetch", args={"q": CANARY}):
            raise CanaryError

        # A model call priced over the ceiling so the cost detection event fires.
        with spanlight.model_span(provider="groq", model="llama-3.3-70b-versatile"):
            spanlight.record_usage(
                tokens_in=412, tokens_out=88, cost_usd=0.0, provider="groq"
            )


def test_no_span_carries_the_canary(spans: InMemorySpanExporter) -> None:
    """SPEC non-goal 4: no prompt or completion bodies leave the process. The
    tool arguments here are the realistic leak, since fingerprinting them is the
    only reason a loop can be reported at all."""
    _exercise_everything()

    leaks = _sweep(spans)
    assert not leaks, "canary escaped in:\n  " + "\n  ".join(leaks)


def test_an_exception_message_never_becomes_an_event(
    spans: InMemorySpanExporter,
) -> None:
    """The leak the canary actually caught, kept as its own named regression.

    OpenTelemetry's `start_as_current_span` defaults `record_exception` and
    `set_status_on_exception` to True, so the SDK attaches an event carrying
    `exception.message` and a full `exception.stacktrace`, and overwrites the
    status description with the message. The `error.type` attribute this library
    sets was correct the whole time; the leak sat beside it, put there by the
    layer underneath.
    """
    with pytest.raises(CanaryError), spanlight.tool_span("fetch"):
        raise CanaryError

    (span,) = spans.get_finished_spans()
    assert span.attributes["error.type"] == "CanaryError"
    assert span.events == ()
    assert not _leaked(span.status.description)


def test_identifiers_are_recorded_verbatim_by_design(
    spans: InMemorySpanExporter,
) -> None:
    """The boundary the canary sweep assumes, stated so it is a decision rather
    than an oversight. A tool name and a retrieval index are chosen by the
    developer, like a table name, and are recorded as given. Everything a user
    supplies goes through a fingerprint instead."""
    with spanlight.session(), spanlight.retrieval_span("schemes-v3", k=3):
        pass

    retrieval = next(s for s in spans.get_finished_spans() if s.name.startswith("retrieve"))
    assert retrieval.attributes["spanlight.retrieval.index"] == "schemes-v3"


def test_the_detections_still_fired(spans: InMemorySpanExporter) -> None:
    """Guards the sweep above. If nothing detected and no events were emitted,
    the canary test would pass by having nothing to search, which is the shape
    of a test that cannot fail."""
    _exercise_everything()

    events = [e for s in spans.get_finished_spans() for e in s.events]
    assert events, "no detection events were emitted, so the sweep proved nothing"


def test_the_sweep_can_actually_find_a_leak(spans: InMemorySpanExporter) -> None:
    """Proves the detector of leaks detects leaks. A sweep that looks in the
    wrong place returns clean forever and reads as a guarantee."""
    with spanlight.session(), spanlight.tool_span("search"):
        spanlight.get_tracer()  # keep the import honest
        from opentelemetry import trace

        trace.get_current_span().set_attribute("deliberate.leak", CANARY)

    assert _sweep(spans), "the sweep failed to notice a planted leak"


def test_metric_labels_never_carry_the_canary(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Metric labels are worse than span attributes for this. A leaked value on
    a span is one trace; on a label it becomes a permanent time series per
    distinct value, and it cannot be deleted by dropping a trace."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    counter = provider.get_meter("test").create_counter(DETECTIONS_TOTAL)
    monkeypatch.setattr(metrics_module, "_counter", lambda: counter)
    metrics_module.set_service(CANARY_SAFE_SERVICE := "test-service")

    _exercise_everything()

    data = reader.get_metrics_data()
    assert data is not None, "no metrics recorded, so this proved nothing"
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    assert not _leaked(dict(point.attributes)), point.attributes
    assert CANARY_SAFE_SERVICE
