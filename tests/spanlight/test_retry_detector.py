from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight._detector_framework import registry
from spanlight._detectors import RETRY_THRESHOLD, retry_amplification_detector
from spanlight.attributes import (
    DETECTION,
    DETECTION_RETRY_FAILED_ATTEMPTS,
    DETECTION_RETRY_THRESHOLD,
)


class Transient(Exception):
    pass


@pytest.fixture(autouse=True)
def detector():
    registry.clear_detectors()
    registry.register(retry_amplification_detector())
    yield
    registry.clear_detectors()
    registry.reset()


def call(failures: int, *, recovers: bool = True) -> None:
    """One model call that failed `failures` times before landing."""
    with spanlight.model_span(provider="groq"):
        for attempt in range(1, failures + 1):
            try:
                with spanlight.attempt_span(attempt):
                    raise Transient("429")
            except Transient:
                pass
        if recovers:
            with spanlight.attempt_span(failures + 1):
                pass


def detections(spans: InMemorySpanExporter) -> list[str]:
    return [
        s.attributes[DETECTION]
        for s in spans.get_finished_spans()
        if DETECTION in (s.attributes or {})
    ]


def test_a_single_transient_retry_is_not_amplification(
    spans: InMemorySpanExporter,
) -> None:
    """The most common thing a free tier does. A detector that fires here gets
    muted in week one and the project's value quietly evaporates."""
    with spanlight.session():
        call(failures=1)

    assert detections(spans) == []


def test_two_calls_retrying_twice_stays_quiet(spans: InMemorySpanExporter) -> None:
    """Four failures, and it does not fire. This pattern was written down as
    healthy in `bench/false_positives.py` before the threshold was measured, and
    the measured threshold had to accommodate it rather than the label being
    changed afterwards to justify a threshold."""
    with spanlight.session():
        call(failures=2)
        call(failures=2)

    assert detections(spans) == []


def test_it_fires_once_the_session_passes_the_threshold(
    spans: InMemorySpanExporter,
) -> None:
    with spanlight.session():
        call(failures=2)
        call(failures=2)
        call(failures=2)

    assert detections(spans) == ["retry_amplification"]


def test_the_count_is_per_session_not_per_call(spans: InMemorySpanExporter) -> None:
    """The reason this counts a session rather than a call. Six calls that each
    retried once is a run burning its budget on backoff, and no single call in
    it looks remarkable."""
    with spanlight.session():
        for _ in range(RETRY_THRESHOLD):
            call(failures=1)

    assert detections(spans) == ["retry_amplification"]


def test_successful_attempts_are_not_counted(spans: InMemorySpanExporter) -> None:
    """Otherwise this measures how much work a session did, and every long run
    trips it."""
    with spanlight.session():
        for _ in range(12):
            call(failures=0)

    assert detections(spans) == []


def test_the_detection_carries_both_numbers(spans: InMemorySpanExporter) -> None:
    """A breach is only meaningful next to the line it crossed, and the
    threshold is a deployment choice a reader six months later will not know."""
    with spanlight.session():
        for _ in range(RETRY_THRESHOLD):
            call(failures=1)

    event = next(
        e
        for s in spans.get_finished_spans()
        for e in s.events
        if e.name == "spanlight.detection"
    )

    assert event.attributes[DETECTION_RETRY_FAILED_ATTEMPTS] == RETRY_THRESHOLD
    assert event.attributes[DETECTION_RETRY_THRESHOLD] == RETRY_THRESHOLD


def test_it_fires_once_per_session_not_once_per_later_failure(
    spans: InMemorySpanExporter,
) -> None:
    """Otherwise a session that keeps failing turns one problem into a count of
    how many attempts happened to follow it, and the counter becomes a measure
    of session length."""
    with spanlight.session():
        for _ in range(RETRY_THRESHOLD * 3):
            call(failures=1)

    assert detections(spans) == ["retry_amplification"]


def test_a_new_session_starts_from_zero(spans: InMemorySpanExporter) -> None:
    """State is session-scoped and released when the session ends. Leaking it
    would make the first call of an unrelated run inherit someone else's
    failures."""
    for _ in range(3):
        with spanlight.session():
            for _ in range(RETRY_THRESHOLD - 1):
                call(failures=1)

    assert detections(spans) == []
