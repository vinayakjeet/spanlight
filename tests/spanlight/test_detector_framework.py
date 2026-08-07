from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight._detector_framework import (
    SESSION,
    Detection,
    DetectorRegistry,
    registry,
)
from spanlight.attributes import DETECTION


def test_dispatches_to_every_registered_detector(spans: InMemorySpanExporter) -> None:
    seen = []
    registry.register(lambda state, span: seen.append(span.name) or None)
    registry.register(lambda state, span: seen.append(span.name) or None)

    with spanlight.session(), spanlight.tool_span("search"):
        pass

    assert seen.count("tool search") == 2


def test_state_is_shared_between_detectors_in_one_session(
    spans: InMemorySpanExporter,
) -> None:
    def writer(state, span):
        state["seen"] = state.get("seen", 0) + 1
        return None

    observed = []
    registry.register(writer)
    registry.register(lambda state, span: observed.append(state.get("seen")) or None)

    with spanlight.session():
        for _ in range(3):
            with spanlight.tool_span("search"):
                pass

    assert observed == [1, 2, 3]


def test_session_phase_detectors_do_not_run_on_ordinary_spans(
    spans: InMemorySpanExporter,
) -> None:
    seen = []
    registry.register(lambda state, span: seen.append(span.name) or None, phase=SESSION)

    with spanlight.session(), spanlight.tool_span("search"):
        pass

    assert seen == ["session"]


def test_a_detector_marks_the_span_it_fired_on(spans: InMemorySpanExporter) -> None:
    registry.register(lambda state, span: Detection("synthetic"))

    with spanlight.session(), spanlight.tool_span("search"):
        pass

    tool = next(s for s in spans.get_finished_spans() if s.name == "tool search")
    assert tool.attributes[DETECTION] == "synthetic"


def test_state_is_released_when_a_session_ends() -> None:
    """The bound that matters in the healthy case. LRU and TTL are the backstop
    for runs that die before they can release."""
    with spanlight.session("run-1"):
        registry.state_for("run-1")["scratch"] = 1
        assert "run-1" in registry._state

    assert "run-1" not in registry._state


def test_lru_cap_evicts_the_least_recently_used_session() -> None:
    bounded = DetectorRegistry(max_sessions=2)
    for session_id in ("a", "b", "c"):
        bounded.state_for(session_id)

    assert set(bounded._state) == {"b", "c"}


def test_touching_a_session_saves_it_from_eviction() -> None:
    bounded = DetectorRegistry(max_sessions=2)
    bounded.state_for("a")
    bounded.state_for("b")
    bounded.state_for("a")
    bounded.state_for("c")

    assert set(bounded._state) == {"a", "c"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_ttl_evicts_a_session_that_never_ended() -> None:
    """A process killed mid-session, or a `session()` whose context was never
    exited, would otherwise leak one entry per run for the life of the process."""
    clock = FakeClock()
    expiring = DetectorRegistry(ttl_seconds=60.0, clock=clock)
    expiring.state_for("abandoned")

    clock.now = 61.0
    expiring.state_for("fresh")

    assert set(expiring._state) == {"fresh"}


def test_the_ttl_runs_from_last_use_not_from_creation() -> None:
    """A long run must not be evicted for being long. Measured from creation,
    an agent still working after the TTL would lose its loop counters and stop
    being watched at the point it had become most worth watching."""
    clock = FakeClock()
    expiring = DetectorRegistry(ttl_seconds=60.0, clock=clock)
    expiring.state_for("long-run")["tool_calls"] = {("search", "abc"): 2}

    for tick in (30.0, 60.0, 90.0, 120.0):
        clock.now = tick
        state = expiring.state_for("long-run")

    assert state == {"tool_calls": {("search", "abc"): 2}}


def test_eviction_never_drops_the_session_being_used() -> None:
    """Regression: `_evict` ran after inserting the new entry and could delete
    it, so the caller received scratch space already gone from the map."""
    clock = FakeClock()
    expiring = DetectorRegistry(ttl_seconds=0.0, clock=clock)
    state = expiring.state_for("run")
    state["scratch"] = 1

    assert expiring.state_for("run") == {"scratch": 1}


def test_detectors_do_not_run_outside_a_session(spans: InMemorySpanExporter) -> None:
    seen = []
    registry.register(lambda state, span: seen.append(span.name) or None)

    with spanlight.model_span(provider="mock"):
        pass

    assert seen == []
