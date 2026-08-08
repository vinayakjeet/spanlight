from __future__ import annotations

import gc
import tracemalloc

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

import spanlight
import spanlight._spans as spans_module
from spanlight._detector_framework import DetectorRegistry, registry
from spanlight._detectors import (
    cost_ceiling_detector,
    loop_detector,
    silent_tool_failure_detector,
    watch_for_silent_failure,
)

SESSIONS = 10_000


class _Discard(SpanExporter):
    """Keeps nothing, so what is measured is Spanlight rather than the test."""

    def export(self, spans) -> SpanExportResult:  # noqa: ARG002
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def _register_everything() -> None:
    registry.register(loop_detector)
    registry.register(watch_for_silent_failure)
    registry.register(cost_ceiling_detector(1.0))
    registry.register(silent_tool_failure_detector, phase="session")


def test_ten_thousand_sessions_leave_nothing_resident(spans) -> None:  # noqa: ARG001
    """M5.3. An agent process is long-lived and sessions are unbounded, so any
    per-session state that is not released is a leak with a slow fuse: it looks
    fine in a test run and takes the service down after a week.

    Every session here ends cleanly, so the LRU and TTL never come into it. This
    asserts the ordinary path returns its memory rather than relying on a bound
    to eventually reclaim it.
    """
    _register_everything()

    for i in range(SESSIONS):
        with spanlight.session():
            with spanlight.tool_span("search", args={"q": f"query-{i}"}):
                pass
            with spanlight.model_span(provider="mock"):
                pass

    assert registry._state == {}


def test_sessions_that_never_end_stay_bounded() -> None:
    """The unhealthy path: a process killed mid-session, or a `session()` whose
    context was never exited, leaves state behind. The LRU cap is what stops
    one leaked entry per run accumulating forever."""
    bounded = DetectorRegistry(max_sessions=64)

    for i in range(SESSIONS):
        bounded.state_for(f"abandoned-{i}")["tool_calls"] = {("search", "fp"): 1}

    assert len(bounded._state) == 64


def test_memory_is_flat_across_ten_thousand_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count staying bounded is not the same as memory staying flat: a bound
    on entries says nothing about what each entry holds, and the loop detector's
    per-session dict grows with every distinct tool call it sees.

    Measured against a warmed baseline, because the first sessions pay for
    interpreter and library allocations that never come back and would otherwise
    read as a leak.

    Deliberately not the `spans` fixture. `InMemorySpanExporter` keeps every span
    it is handed, so at 10,000 sessions the test itself retains 20,000 spans and
    reports about 55MB of its own bookkeeping as a Spanlight leak. Measuring
    memory through a component whose job is to accumulate measures the component.
    """
    exporter = _Discard()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(spans_module, "get_tracer", lambda: provider.get_tracer("test"))

    _register_everything()

    def run(n: int) -> None:
        for i in range(n):
            with (
                spanlight.session(),
                spanlight.tool_span("search", args={"q": f"query-{i}"}),
            ):
                pass

    run(500)
    gc.collect()

    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    run(SESSIONS)
    gc.collect()
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    grown = sum(s.size_diff for s in after.compare_to(before, "filename"))

    # Measured at 928 bytes total, 0.1 per session. The threshold sits far above
    # that for tracemalloc noise, but far below proportional growth: anything
    # retaining even 50 bytes per session fails here, and 1KB each would miss by
    # twenty times. The claim is flat, not small.
    assert grown < 500_000, f"{grown:,} bytes retained across {SESSIONS:,} sessions"


def test_a_single_long_session_does_not_grow_without_limit(spans) -> None:  # noqa: ARG001
    """The other shape of the same leak. One session making many distinct tool
    calls accumulates a fingerprint per call in the loop detector's counter,
    which is per-session state that nothing evicts until the session ends."""
    _register_everything()

    with spanlight.session():
        for i in range(5_000):
            with spanlight.tool_span("search", args={"q": f"query-{i}"}):
                pass

        counts = registry.state_for(spanlight.current_session_id())["tool_calls"]
        resident = len(counts)

    # Honest about what this is: unbounded within a session, by design. Loop
    # detection cannot work without remembering what it has already seen, and
    # bounding it would make the detector miss loops in exactly the long runs
    # most likely to contain one. The bound is the session ending.
    assert resident == 5_000
    assert registry._state == {}
