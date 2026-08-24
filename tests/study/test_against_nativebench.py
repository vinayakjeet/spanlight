from __future__ import annotations

import pytest

from study.against_nativebench import (
    attempt_spans,
    failure_costs,
    inference_dominance,
    models_served,
)

CHEAP = 0.00008


def run(
    *,
    status: str = "UNSET",
    model_ms: float = 500.0,
    measured_ms: float | None = 520.0,
    session_ms: float = 2000.0,
    cost: float = CHEAP,
    response_model: str = "llama-3.3-70b-versatile",
) -> dict:
    item_attributes: dict = {"spanlight.session.id": "abc"}
    if measured_ms is not None:
        item_attributes["shipgate.latency_ms"] = measured_ms
    return {
        "item": {"status": status, "duration_ms": session_ms, "attributes": item_attributes},
        "model_calls": [
            {
                "duration_ms": model_ms,
                "attributes": {
                    "gen_ai.response.model": response_model,
                    "spanlight.cost_usd_equivalent": cost,
                },
            }
        ],
        "detections": set(),
    }


def test_dominance_uses_the_runner_measurement_not_the_session_span() -> None:
    """The whole point of the two denominators. The session span holds the
    batch semaphore, so a session that queued as long as it ran would report
    50% dominance while every millisecond of actual work was inference."""
    runs = {"s1": run(), "s2": run()}
    stats = inference_dominance(runs)
    assert stats["measured"]["median"] == pytest.approx(500 / 520, abs=0.01)
    assert stats["session_span"]["median"] == pytest.approx(500 / 2000, abs=0.01)


def test_sessions_without_the_clean_measurement_fall_back_to_the_span() -> None:
    runs = {"s1": run(measured_ms=None)}
    stats = inference_dominance(runs)
    assert "measured" not in stats
    assert stats["session_span"]["n"] == 1


def test_failure_cost_ratio_needs_failures_on_both_sides() -> None:
    """None means unmeasurable, never zero. A corpus with no failed sessions
    must not be able to publish 'failures cost the same', because that sentence
    requires having seen one."""
    clean_only = {"s1": run(status="UNSET")}
    assert failure_costs(clean_only) == (None, CHEAP)


def test_a_failed_session_is_priced_against_the_clean_median() -> None:
    runs = {
        f"clean-{i}": run(status="UNSET") for i in range(3)
    } | {"failed-1": run(status="ERROR", cost=CHEAP * 10)}
    failed, clean = failure_costs(runs)
    assert failed == CHEAP * 10
    assert clean == CHEAP


def test_parameter_paradox_needs_two_models() -> None:
    mixed = {
        "a": run(response_model="llama-3.1-8b-instant"),
        "b": run(response_model="llama-3.3-70b-versatile"),
    }
    single = {"a": run()}
    assert len(models_served(mixed)) == 2
    assert len(models_served(single)) == 1


def test_attempt_spans_count_only_retry_children() -> None:
    spans = [
        {"name": "chat"},
        {"name": "attempt 1"},
        {"name": "attempt 2"},
        {"name": "session"},
    ]
    assert attempt_spans(spans) == 2
