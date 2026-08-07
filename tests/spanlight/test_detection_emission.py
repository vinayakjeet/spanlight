from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
import spanlight._metrics as metrics_module
from spanlight._detector_framework import registry
from spanlight._detectors import cost_ceiling_detector, loop_detector
from spanlight.attributes import (
    DETECTION,
    DETECTION_COST_CEILING_USD,
    DETECTION_COST_USD_EQUIVALENT,
    DETECTION_EVENT,
    DETECTION_TOOL_CALLS,
    DETECTION_TOOL_NAME,
    DETECTION_TYPE,
    DETECTIONS_TOTAL,
)

TOKENS_IN, TOKENS_OUT = 412, 88
CALL_USD = spanlight.cost_usd_equivalent("groq", TOKENS_IN, TOKENS_OUT)


@pytest.fixture
def counts(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    """Give each test its own meter without touching the global provider.

    `set_meter_provider` is write-once: a second call logs "Overriding of
    current MeterProvider is not allowed" and is ignored, so a suite that
    installed one per test would have every test after the first silently
    reading the first test's reader. That is the same trap the `spans` fixture
    exists to avoid, and it fails by passing.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    counter = provider.get_meter("test").create_counter(DETECTIONS_TOTAL)
    monkeypatch.setattr(metrics_module, "_counter", lambda: counter)
    return reader


def _detections(reader: InMemoryMetricReader) -> dict[tuple[str, str], int]:
    found = {}
    data = reader.get_metrics_data()
    if data is None:
        return found
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name != DETECTIONS_TOTAL:
                    continue
                for point in metric.data.data_points:
                    key = (point.attributes["type"], point.attributes["service"])
                    found[key] = found.get(key, 0) + point.value
    return found


def _loop(times: int = 3) -> None:
    for _ in range(times):
        with spanlight.tool_span("search", args={"q": "pm-kisan"}):
            pass


def test_a_detection_adds_an_event_naming_the_reason(
    spans: InMemorySpanExporter,
) -> None:
    """The attribute says a run tripped something. The event says what, so the
    reader is not left re-deriving the arithmetic the detector already did."""
    registry.register(loop_detector)

    with spanlight.session():
        _loop()

    flagged = next(s for s in spans.get_finished_spans() if DETECTION in s.attributes)
    (event,) = flagged.events
    assert event.name == DETECTION_EVENT
    assert event.attributes[DETECTION_TYPE] == "loop"
    assert event.attributes[DETECTION_TOOL_NAME] == "search"
    assert event.attributes[DETECTION_TOOL_CALLS] == 3


def test_the_event_never_carries_the_arguments(spans: InMemorySpanExporter) -> None:
    """The fingerprint exists so a loop can be reported without the trace
    carrying what was searched for. An event that undid that would be worse than
    no event, because it would look private while not being."""
    registry.register(loop_detector)

    with spanlight.session():
        for _ in range(3):
            with spanlight.tool_span("search", args={"q": "alice@example.com"}):
                pass

    flagged = next(s for s in spans.get_finished_spans() if DETECTION in s.attributes)
    assert "alice@example.com" not in str(flagged.events)


def test_a_cost_breach_records_both_the_total_and_the_line_it_crossed(
    spans: InMemorySpanExporter,
) -> None:
    ceiling = CALL_USD * 1.5
    registry.register(cost_ceiling_detector(ceiling))

    with spanlight.session():
        for _ in range(2):
            with spanlight.model_span(provider="groq"):
                spanlight.record_usage(
                    tokens_in=TOKENS_IN,
                    tokens_out=TOKENS_OUT,
                    cost_usd=0.0,
                    provider="groq",
                )

    flagged = next(s for s in spans.get_finished_spans() if DETECTION in s.attributes)
    (event,) = flagged.events
    assert event.attributes[DETECTION_COST_CEILING_USD] == ceiling
    assert event.attributes[DETECTION_COST_USD_EQUIVALENT] == pytest.approx(
        CALL_USD * 2
    )


def test_a_detection_increments_the_counter(
    spans: InMemorySpanExporter, counts: InMemoryMetricReader
) -> None:
    """SPEC S4 through S6 all promise a counter, because an alert needs
    something evaluable on a schedule and alerting on a span means querying
    traces on a timer."""
    metrics_module.set_service("test-service")
    registry.register(loop_detector)

    with spanlight.session():
        _loop()

    assert _detections(counts)[("loop", "test-service")] == 1


def test_the_counter_counts_sessions_not_steps(
    spans: InMemorySpanExporter, counts: InMemoryMetricReader
) -> None:
    """Three runs that each loop should read as three problems. If the fire-once
    rule leaked, a single long run would outrank several short broken ones and
    the alert would rank by session length."""
    metrics_module.set_service("test-service")
    registry.register(loop_detector)

    for _ in range(3):
        with spanlight.session():
            _loop(times=6)

    assert _detections(counts)[("loop", "test-service")] == 3


def test_a_clean_run_increments_nothing(
    spans: InMemorySpanExporter, counts: InMemoryMetricReader
) -> None:
    registry.register(loop_detector)

    with spanlight.session():
        _loop(times=2)

    assert _detections(counts) == {}


PROXY_PROBE = """
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry import metrics

from spanlight._metrics import count_detection, set_service

# Counter is built here, before any provider exists, exactly as it is in a real
# process where the module is imported long before init() runs.
count_detection("before")

reader = InMemoryMetricReader()
metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
set_service("probe")
count_detection("after")

seen = {
    point.attributes["type"]
    for resource in reader.get_metrics_data().resource_metrics
    for scope in resource.scope_metrics
    for metric in scope.metrics
    for point in metric.data.data_points
}
print(sorted(seen))
"""


def test_the_counter_forwards_once_a_provider_appears() -> None:
    """`_metrics` caches its counter at first use, which is only safe because
    `get_meter` returns a proxy that binds to whichever provider arrives later.
    If that stopped holding, the counter would pin a no-op meter and every
    detection would be silently uncounted while the spans still looked right.

    In a subprocess because `set_meter_provider` is write-once per process, so
    the rest of this module patches around it and cannot prove this.
    """
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(PROXY_PROBE)],
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    # "before" is genuinely lost, which is correct: nothing was listening yet.
    assert result.stdout.strip() == "['after']"
