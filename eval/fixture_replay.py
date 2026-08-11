"""Replay recorded session shapes through the real detector chain.

The field corpus is 500 real sessions and it contains no tool spans at all, so
replaying it exercises `cost_ceiling` and nothing else. `eval/detector_gate.py`
says so in its own output: three of the four numbers it reports sit at a floor
and cannot move. A gate that protects a quarter of the thing it guards is worth
knowing about rather than trusting.

These fixtures cover the rest. Each one describes what an agent *did*, in steps a
reader can check against the rule it is meant to trip, rather than describing
spans and their attributes. That matters for the same reason the corpus is
replayed rather than read: a fixture written in terms of the library's own output
agrees with whatever the library currently does, and would keep agreeing after it
broke.

Positive and negative for every detector, because a corpus of failures cannot
tell a working detector from one that fires on everything, and a corpus of
healthy sessions cannot tell it from one that never fires.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
import spanlight._spans as spans_module
from spanlight._detector_framework import SESSION, registry
from spanlight._detectors import (
    cost_ceiling_detector,
    loop_detector,
    retry_amplification_detector,
    silent_tool_failure_detector,
    watch_for_silent_failure,
)
from spanlight.attributes import DETECTION

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# High enough that no fixture trips it by accident. A fixture meant to test the
# loop rule that also crosses a cost ceiling would pass for the wrong reason.
FIXTURE_CEILING_USD = 1.0


class StepFailed(Exception):
    """Raised inside a step marked `fails`, and swallowed by the replayer.

    A tool that raises and is caught is the shape the silent-failure rule reads,
    so the exception has to be real rather than a status set by hand.
    """


def load() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("*.json"))
    ]


def _step(step: dict[str, Any]) -> None:
    kind = step["kind"]
    fails = step.get("fails", False)

    if kind == "tool":
        try:
            with spanlight.tool_span(step["name"], args=step.get("args")):
                if fails:
                    raise StepFailed(step["name"])
        except StepFailed:
            pass
        return

    if kind == "retrieval":
        with spanlight.retrieval_span(step["name"], k=step.get("k")):
            pass
        return

    if kind == "model":
        with spanlight.model_span(provider=step.get("provider", "groq")):
            for number, attempt in enumerate(step.get("attempts", [{}]), start=1):
                try:
                    with spanlight.attempt_span(number):
                        if attempt.get("fails"):
                            raise StepFailed("attempt")
                except StepFailed:
                    pass
            spanlight.record_usage(
                tokens_in=step.get("tokens_in", 400),
                tokens_out=step.get("tokens_out", 100),
                cost_usd=0.0,
                provider=step.get("provider", "groq"),
            )
        return

    raise ValueError(f"unknown step kind {kind!r} in a fixture")


def replay(fixture: dict, ceiling_usd: float = FIXTURE_CEILING_USD) -> set[str]:
    """Run one fixture and return the detection types it produced.

    Builds its own provider and restores the tracer afterwards, so a caller that
    already has tracing configured is not left with this one.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    original = spans_module.get_tracer
    spans_module.get_tracer = lambda: provider.get_tracer("fixture-replay")

    registry.clear_detectors()
    registry.register(loop_detector)
    registry.register(retry_amplification_detector())
    registry.register(watch_for_silent_failure)
    registry.register(silent_tool_failure_detector, phase=SESSION)
    registry.register(cost_ceiling_detector(fixture.get("ceiling_usd", ceiling_usd)))

    try:
        with spanlight.session():
            for step in fixture["steps"]:
                _step(step)
        return {
            span.attributes[DETECTION]
            for span in exporter.get_finished_spans()
            if DETECTION in (span.attributes or {})
        }
    finally:
        spans_module.get_tracer = original
        registry.clear_detectors()
        registry.reset()


def check(fixture: dict) -> str | None:
    """None when the fixture behaved, otherwise why it did not."""
    fired = replay(fixture)
    detector = fixture["detector"]

    if fixture["expect"] == "fires":
        if detector not in fired:
            return f"{fixture['name']}: expected {detector}, got {sorted(fired) or 'nothing'}"
        return None

    if detector in fired:
        return f"{fixture['name']}: {detector} fired on a session that is fine"
    return None
