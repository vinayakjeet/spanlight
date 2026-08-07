from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight._detector_framework import registry
from spanlight._detectors import cost_ceiling_detector
from spanlight.attributes import DETECTION

TOKENS_IN, TOKENS_OUT = 412, 88

# Derived from the price table rather than written down, so that editing
# list_prices.yaml cannot leave a stale constant here quietly deciding which
# span the ceiling lands on. A ceiling at 1.5 calls crosses on the second.
CALL_USD = spanlight.cost_usd_equivalent("groq", TOKENS_IN, TOKENS_OUT)
CEILING_USD = CALL_USD * 1.5


@pytest.fixture(autouse=True)
def _registered() -> None:
    registry.register(cost_ceiling_detector(CEILING_USD))


def _call() -> None:
    with spanlight.model_span(provider="groq"):
        spanlight.record_usage(
            tokens_in=TOKENS_IN, tokens_out=TOKENS_OUT, cost_usd=0.0, provider="groq"
        )


def test_fires_on_the_call_that_crosses_the_ceiling(
    spans: InMemorySpanExporter,
) -> None:
    with spanlight.session():
        _call()
        _call()

    first, second = [s for s in spans.get_finished_spans() if s.name == "chat"]
    assert DETECTION not in first.attributes
    assert second.attributes[DETECTION] == "cost_ceiling"


def test_does_not_fire_below_the_ceiling(spans: InMemorySpanExporter) -> None:
    with spanlight.session():
        _call()

    for span in spans.get_finished_spans():
        assert DETECTION not in span.attributes


def test_fires_once_not_on_every_later_span(spans: InMemorySpanExporter) -> None:
    """A ceiling stays crossed for the rest of the run. Re-reporting it on each
    following span would turn one problem into a count of the steps after it,
    and make `spanlight_detections_total` a measure of session length."""
    with spanlight.session():
        for _ in range(5):
            _call()

    flagged = [
        s for s in spans.get_finished_spans() if s.attributes.get(DETECTION) is not None
    ]
    assert len(flagged) == 1


def test_the_run_continues_after_firing(spans: InMemorySpanExporter) -> None:
    """SPEC S6. Detectors observe; they never cancel a run or cap spend."""
    completed = 0
    with spanlight.session():
        for _ in range(4):
            _call()
            completed += 1

    assert completed == 4
    assert len([s for s in spans.get_finished_spans() if s.name == "chat"]) == 4


def test_free_tier_spend_alone_could_never_fire(spans: InMemorySpanExporter) -> None:
    """The reason the detector reads the counterfactual. Every price in the
    chassis quotas.yaml is zero, so a ceiling watching real spend sits at zero
    forever, and its tests pass while it is incapable of firing."""
    with spanlight.session():
        for _ in range(5):
            _call()

    charged = [
        s.attributes.get("spanlight.cost_usd")
        for s in spans.get_finished_spans()
        if s.name == "chat"
    ]
    assert set(charged) == {0.0}
