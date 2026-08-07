"""Measure what instrumenting a call actually costs.

Every number in the README comes from here. Run it and the README's Benchmarks
section is reproducible:

    uv run python bench/overhead.py

Measures in-process cost only. The exporter's network time is deliberately
excluded, because a `BatchSpanProcessor` hands spans to a background thread and
the caller never waits for a request; including it would measure Grafana's
latency and call it Spanlight's overhead.

Runs disabled first and enabled second in the same process on purpose.
`set_tracer_provider` is write-once, so the disabled measurement has to happen
before anything installs a provider.
"""

from __future__ import annotations

import platform
import statistics
import timeit

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

import spanlight
from spanlight._detector_framework import SESSION, registry
from spanlight._detectors import (
    cost_ceiling_detector,
    loop_detector,
    silent_tool_failure_detector,
    watch_for_silent_failure,
)

ITERATIONS = 20_000
REPEATS = 7

# SPEC S2 commits to this for the disabled path.
S2_BUDGET_US = 50.0


class Discard(SpanExporter):
    """Isolates in-process cost from the network the real exporter would use."""

    def export(self, spans) -> SpanExportResult:  # noqa: ARG002
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def _microseconds_per_call(statement, setup=lambda: None) -> tuple[float, float]:
    setup()
    timings = timeit.repeat(statement, repeat=REPEATS, number=ITERATIONS)
    per_call_us = [t / ITERATIONS * 1_000_000 for t in timings]
    return min(per_call_us), statistics.median(per_call_us)


def _model_span() -> None:
    with spanlight.model_span(provider="bench"):
        pass


def _tool_span() -> None:
    with spanlight.tool_span("bench", args={"q": "x"}):
        pass


def _session() -> None:
    with spanlight.session():
        pass


def _register_detectors() -> None:
    registry.clear_detectors()
    registry.register(loop_detector)
    registry.register(watch_for_silent_failure)
    registry.register(cost_ceiling_detector(1.0))
    registry.register(silent_tool_failure_detector, phase=SESSION)


def main() -> None:
    rows: list[tuple[str, float, float]] = []

    # Disabled. No provider is configured, so OpenTelemetry hands back a
    # non-recording span. This has to run before anything installs a provider.
    rows.append(("model_span, tracing disabled", *_microseconds_per_call(_model_span)))
    rows.append(("session, tracing disabled", *_microseconds_per_call(_session)))

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(Discard()))
    from opentelemetry import trace

    trace.set_tracer_provider(provider)

    rows.append(("model_span, tracing enabled", *_microseconds_per_call(_model_span)))
    rows.append(("tool_span, tracing enabled", *_microseconds_per_call(_tool_span)))
    rows.append(("session, tracing enabled", *_microseconds_per_call(_session)))

    # Detectors run inside the span helper, on the caller's thread, so their cost
    # is the caller's. M5 owns the budget; this is what it will be measured
    # against.
    rows.append(
        (
            "tool_span, enabled, 3 detectors",
            *_microseconds_per_call(_tool_span, setup=_register_detectors),
        )
    )

    # Printed with the numbers because they are meaningless without it, and a
    # README quoting microseconds with no machine attached invites the reader to
    # assume they hold on theirs.
    print(f"{platform.python_implementation()} {platform.python_version()}")
    print(f"{platform.system()} {platform.release()}, {platform.machine()}")
    print(f"{ITERATIONS:,} calls per repeat, best of {REPEATS}")
    print()

    width = max(len(name) for name, _, _ in rows)
    print(f"{'':{width}}   {'best':>9}  {'median':>9}")
    for name, best, median in rows:
        print(f"{name:{width}}   {best:8.2f}us {median:8.2f}us")

    disabled_median = rows[0][2]
    verdict = "within" if disabled_median < S2_BUDGET_US else "OVER"
    print()
    print(
        f"SPEC S2 budget for the disabled path: {S2_BUDGET_US:.0f}us. "
        f"Measured {disabled_median:.2f}us, {verdict}."
    )


if __name__ == "__main__":
    main()
