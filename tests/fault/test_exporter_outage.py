from __future__ import annotations

import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

import spanlight
import spanlight._metrics as metrics_module
import spanlight._spans as spans_module
from fault import Fault, faulty_endpoint
from spanlight._export import CountedExporter
from spanlight.attributes import EXPORT_FAILURES_TOTAL

# Short enough that a hanging endpoint fails the test run rather than stalling it.
EXPORT_TIMEOUT_S = 2


@pytest.fixture
def failures(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    counter = provider.get_meter("test").create_counter(EXPORT_FAILURES_TOTAL)
    monkeypatch.setattr(metrics_module, "_export_failures", lambda: counter)
    return reader


def _reasons(reader: InMemoryMetricReader) -> dict[str, int]:
    found: dict[str, int] = {}
    data = reader.get_metrics_data()
    if data is None:
        return found
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name != EXPORT_FAILURES_TOTAL:
                    continue
                for point in metric.data.data_points:
                    reason = point.attributes["reason"]
                    found[reason] = found.get(reason, 0) + point.value
    return found


def _run_against(endpoint: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Do a normal agent run exporting to `endpoint`, and return its reply.

    Deliberately not `spanlight.init()`: that sets the global tracer provider,
    which is write-once and would leak into the rest of the suite.
    """
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=EXPORT_TIMEOUT_S)
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(CountedExporter(exporter)))
    monkeypatch.setattr(spans_module, "get_tracer", lambda: provider.get_tracer("test"))

    with spanlight.session():
        with spanlight.tool_span("search", args={"q": "pm-kisan"}):
            pass
        with spanlight.model_span(provider="mock"):
            spanlight.record_usage(
                tokens_in=10, tokens_out=5, cost_usd=0.0, provider="mock"
            )
        answer = "the agent finished"

    provider.force_flush(timeout_millis=(EXPORT_TIMEOUT_S + 3) * 1000)
    return answer


def test_an_unreachable_endpoint_does_not_reach_the_caller(
    monkeypatch: pytest.MonkeyPatch, failures: InMemoryMetricReader
) -> None:
    """SPEC S3, first fault. Nothing is listening at all."""
    with faulty_endpoint(Fault.UNREACHABLE) as server:
        assert _run_against(server.url, monkeypatch) == "the agent finished"

    assert _reasons(failures)


def test_a_500_does_not_reach_the_caller(
    monkeypatch: pytest.MonkeyPatch, failures: InMemoryMetricReader
) -> None:
    """SPEC S3, second fault. The endpoint is up and refusing."""
    with faulty_endpoint(Fault.SERVER_ERROR) as server:
        assert _run_against(server.url, monkeypatch) == "the agent finished"
        assert server.requests, "the exporter never actually tried"

    assert _reasons(failures)


def test_a_hanging_endpoint_does_not_reach_the_caller(
    monkeypatch: pytest.MonkeyPatch, failures: InMemoryMetricReader
) -> None:
    """SPEC S3, third fault, and the one that matters most.

    A dead endpoint fails fast. One that accepts the connection and then never
    answers is what actually takes services down, because the caller waits.
    """
    with faulty_endpoint(Fault.HANG, hang_seconds=30) as server:
        assert _run_against(server.url, monkeypatch) == "the agent finished"

    assert _reasons(failures)


def test_the_failure_reason_is_a_closed_set_not_a_message(
    monkeypatch: pytest.MonkeyPatch, failures: InMemoryMetricReader
) -> None:
    """This is a metric label. An exception message here would create a time
    series per distinct error string, and a failing endpoint produces plenty."""
    with faulty_endpoint(Fault.UNREACHABLE) as server:
        _run_against(server.url, monkeypatch)

    reasons = set(_reasons(failures))
    assert reasons <= {"rejected", "timeout", "connection", "unknown"}, reasons


def test_a_healthy_endpoint_counts_nothing(
    monkeypatch: pytest.MonkeyPatch, failures: InMemoryMetricReader
) -> None:
    """The counter has to be able to stay at zero, or it is measuring the fact
    that an export happened rather than that one failed."""
    from tests.spanlight.collector import collector

    with collector() as (server, endpoint):
        assert _run_against(endpoint, monkeypatch) == "the agent finished"
        assert server.paths == ["/v1/traces"]

    assert _reasons(failures) == {}
