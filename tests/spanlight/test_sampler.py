from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

import spanlight
import spanlight._spans as spans_module
from spanlight._detector_framework import SESSION, registry
from spanlight._detectors import (
    cost_ceiling_detector,
    loop_detector,
    silent_tool_failure_detector,
    watch_for_silent_failure,
)
from spanlight.attributes import SESSION_ID

STEPS_PER_SESSION = 3


def _sampled(monkeypatch: pytest.MonkeyPatch, rate: float, sessions: int) -> list:
    """Run whole sessions through a genuinely sampling provider.

    The `spans` fixture builds its provider with no sampler, so nothing in the
    rest of the suite ever exercises one. That is how a sampler that raised
    `TypeError` on its own happy path stayed green here and only surfaced when
    exporting to Grafana.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ParentBased(root=TraceIdRatioBased(rate)))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(spans_module, "get_tracer", lambda: provider.get_tracer("test"))

    for _ in range(sessions):
        with spanlight.session():
            with spanlight.model_span(provider="mock"):
                pass
            with spanlight.tool_span("search"):
                pass

    return exporter.get_finished_spans()


def test_rate_one_keeps_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(_sampled(monkeypatch, 1.0, 5)) == 5 * STEPS_PER_SESSION


def test_rate_zero_keeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(_sampled(monkeypatch, 0.0, 5)) == 0


def test_a_session_is_never_exported_in_half(monkeypatch: pytest.MonkeyPatch) -> None:
    """SPEC S8. A session that arrives missing its children reads as a run that
    did less work than it did, which is worse than not arriving at all: the M7
    study would score it as a short successful session."""
    spans = _sampled(monkeypatch, 0.5, 200)

    per_session: dict[str, int] = {}
    for span in spans:
        session_id = span.attributes[SESSION_ID]
        per_session[session_id] = per_session.get(session_id, 0) + 1

    assert set(per_session.values()) == {STEPS_PER_SESSION}


def test_a_dropped_session_does_not_take_the_host_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two suites missed this from opposite sides. Nothing here registered a
    detector, and nothing in the detector tests sampled, so a host running below
    rate 1.0 raised `AttributeError` out of instrumentation on its first dropped
    session: a sampled-out span is a `NonRecordingSpan`, which has no
    `attributes` for a detector to read.

    Registering the real default set, because a defensive detector written for
    this test would prove only that the test's detector is safe."""
    registry.clear_detectors()
    registry.register(loop_detector)
    registry.register(watch_for_silent_failure)
    registry.register(silent_tool_failure_detector, phase=SESSION)
    registry.register(cost_ceiling_detector(0.0))
    try:
        assert len(_sampled(monkeypatch, 0.0, 3)) == 0
    finally:
        registry.clear_detectors()
        registry.reset()


def test_the_rate_is_roughly_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loose bounds on purpose. This is asserting that the rate is wired through
    at all, not re-testing OpenTelemetry's ratio arithmetic, and a tight bound on
    a random process buys a flaky suite rather than a stronger claim."""
    sessions = 1_000
    kept = len(_sampled(monkeypatch, 0.1, sessions)) / STEPS_PER_SESSION

    assert 0.05 * sessions < kept < 0.16 * sessions
