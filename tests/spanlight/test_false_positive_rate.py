from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from bench.false_positives import PATTERNS, measure, wilson
from spanlight._detector_framework import SESSION, registry
from spanlight._detectors import (
    loop_detector,
    retry_amplification_detector,
    silent_tool_failure_detector,
    watch_for_silent_failure,
)
from spanlight.attributes import DETECTION

# Measured at 0/700 for both. The budget is not zero, because a detector tuned
# until it never fires on anything is a detector that never fires. It is low
# enough that reintroducing either bug found in M6.3 fails here: both misfired on
# 100% of at least one pattern, which is 14% and 29% overall.
MAX_FALSE_POSITIVE_RATE = 0.02

# Smaller than the bench's 100 so the suite stays quick. Still enough that a
# pattern misfiring at 100% cannot hide.
SESSIONS_PER_PATTERN = 25


class ToolBroke(Exception):
    pass


@pytest.fixture(autouse=True)
def _registered() -> None:
    registry.register(loop_detector)
    registry.register(retry_amplification_detector())
    registry.register(watch_for_silent_failure)
    registry.register(silent_tool_failure_detector, phase=SESSION)


def test_healthy_sessions_stay_below_the_false_positive_budget() -> None:
    """M6.3's regression guard. A later tuning change that reintroduces noise
    fails here rather than in a user's dashboard.

    ShipGate set a gate threshold of 2 points by intuition against a judge whose
    measured noise floor was 20, and every run it flagged was noise. The same
    mistake was in `LOOP_THRESHOLD` until this was measured.
    """
    results = measure(sessions_per_pattern=SESSIONS_PER_PATTERN)
    # Positive controls are in there to prove the detectors can fire at all, and
    # counting them here would report a working detector as a noisy one.
    healthy = {name: counts for name, counts in results.items() if name in PATTERNS}
    total = sum(counts["sessions"] for counts in healthy.values())

    for kind in ("loop", "silent_tool_failure", "retry_amplification"):
        fired = sum(counts[kind] for counts in healthy.values())
        _, upper = wilson(fired, total)
        assert fired / total <= MAX_FALSE_POSITIVE_RATE, (
            f"{kind} fired on {fired}/{total} healthy sessions "
            f"(95% CI upper bound {upper:.1%}); "
            f"worst pattern: {max(results, key=lambda p: results[p][kind])}"
        )


def test_a_retry_is_not_a_loop(spans: InMemorySpanExporter) -> None:
    """The exact false positive measurement found, kept as its own case.

    Two failures and a success send identical arguments three times. Counting
    failed calls made that indistinguishable from an agent stuck asking the same
    question, and it fired on every retry session in the corpus.
    """
    with spanlight.session():
        for attempt in range(3):
            if attempt < 2:
                with pytest.raises(ToolBroke), spanlight.tool_span("search", args={"q": "x"}):
                    raise ToolBroke
            else:
                with spanlight.tool_span("search", args={"q": "x"}):
                    pass

    for span in spans.get_finished_spans():
        assert DETECTION not in span.attributes


def test_three_successful_identical_calls_are_still_a_loop(
    spans: InMemorySpanExporter,
) -> None:
    """The other half. Excluding failures must not stop the detector detecting."""
    with spanlight.session():
        for _ in range(3):
            with spanlight.tool_span("search", args={"q": "x"}):
                pass

    assert any(
        span.attributes.get(DETECTION) == "loop" for span in spans.get_finished_spans()
    )


def test_recovering_from_a_failed_tool_is_not_a_silent_failure(
    spans: InMemorySpanExporter,
) -> None:
    """An agent that hits a broken tool, succeeds with another, and answers from
    real data handled the failure. It did not hide it."""
    with spanlight.session():
        with pytest.raises(ToolBroke), spanlight.tool_span("search", args={"q": "x"}):
            raise ToolBroke
        with spanlight.tool_span("lookup_scheme", args={"id": 42}):
            pass
        with spanlight.model_span(provider="mock"):
            pass

    session_span = next(s for s in spans.get_finished_spans() if s.name == "session")
    assert DETECTION not in session_span.attributes


def test_answering_from_the_model_alone_is_still_a_silent_failure(
    spans: InMemorySpanExporter,
) -> None:
    """The true positive the recovery clause must not swallow: the tool failed,
    nothing else succeeded, and the run answered anyway."""
    with spanlight.session():
        with pytest.raises(ToolBroke), spanlight.tool_span("search", args={"q": "x"}):
            raise ToolBroke
        with spanlight.model_span(provider="mock"):
            pass

    session_span = next(s for s in spans.get_finished_spans() if s.name == "session")
    assert session_span.attributes[DETECTION] == "silent_tool_failure"
