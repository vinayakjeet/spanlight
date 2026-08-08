from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

import spanlight._metrics as metrics_module
from spanlight._export import CountedExporter
from spanlight.attributes import EXPORT_FAILURES_TOTAL

# Tested directly rather than through a BatchSpanProcessor, because the processor
# runs export on a background thread and catches everything it throws. Anything
# asserted from out there passes whether or not this wrapper does its job: an
# earlier version of these tests survived deleting the counting entirely.


class Raises(SpanExporter):
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def export(self, spans) -> SpanExportResult:  # noqa: ARG002
        raise self.exc

    def shutdown(self) -> None:
        pass


class Rejects(SpanExporter):
    def export(self, spans) -> SpanExportResult:  # noqa: ARG002
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass


class Succeeds(SpanExporter):
    def export(self, spans) -> SpanExportResult:  # noqa: ARG002
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


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


def test_a_raising_exporter_does_not_raise_through(
    failures: InMemoryMetricReader,
) -> None:
    """The wrapper absorbs it and reports FAILURE. An exporter that throws at its
    caller is how instrumentation takes down the thing it is watching."""
    result = CountedExporter(Raises(ConnectionRefusedError("nope"))).export([])

    assert result is SpanExportResult.FAILURE


def test_a_raising_exporter_is_counted(failures: InMemoryMetricReader) -> None:
    CountedExporter(Raises(ConnectionRefusedError("nope"))).export([])

    assert _reasons(failures) == {"connection": 1}


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (TimeoutError("slow"), "timeout"),
        (ConnectionRefusedError("refused"), "connection"),
        (ValueError("something else"), "unknown"),
    ],
)
def test_the_reason_is_classified_not_quoted(
    exc: Exception, reason: str, failures: InMemoryMetricReader
) -> None:
    """`reason` is a metric label. Putting the exception message here would make
    one time series per distinct error string, and a failing endpoint produces
    plenty of them."""
    CountedExporter(Raises(exc)).export([])

    assert _reasons(failures) == {reason: 1}


def test_a_rejected_batch_is_counted(failures: InMemoryMetricReader) -> None:
    """The path every real OTLP failure takes, since that exporter catches its
    own errors and returns FAILURE rather than raising."""
    result = CountedExporter(Rejects()).export([])

    assert result is SpanExportResult.FAILURE
    assert _reasons(failures) == {"rejected": 1}


def test_a_successful_export_counts_nothing(failures: InMemoryMetricReader) -> None:
    """Or the counter measures exports rather than failures."""
    result = CountedExporter(Succeeds()).export([])

    assert result is SpanExportResult.SUCCESS
    assert _reasons(failures) == {}


def test_shutdown_and_flush_reach_the_wrapped_exporter() -> None:
    """A wrapper that quietly drops these leaves spans unflushed at exit, which
    would look exactly like the batch timer never firing."""
    calls = []

    class Recording(SpanExporter):
        def export(self, spans) -> SpanExportResult:  # noqa: ARG002
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            calls.append("shutdown")

        def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: ARG002
            calls.append("force_flush")
            return True

    exporter = CountedExporter(Recording())
    exporter.force_flush()
    exporter.shutdown()

    assert calls == ["force_flush", "shutdown"]
