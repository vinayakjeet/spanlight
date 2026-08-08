from __future__ import annotations

from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from spanlight._metrics import count_export_failure

REJECTED = "rejected"
TIMEOUT = "timeout"
CONNECTION = "connection"
UNKNOWN = "unknown"


class CountedExporter(SpanExporter):
    """Wraps an exporter so its failures are counted instead of silent.

    `BatchSpanProcessor` calls `export` on a background thread and discards
    whatever it returns. That is the correct behaviour, and it is what makes
    SPEC S3 true: a dead endpoint cannot reach the caller. It also means a
    service whose exports have failed for a week looks exactly like a service
    that has been quiet for a week, from inside and from outside.

    Absence is not alertable. There is no query that distinguishes "no traces
    because nothing ran" from "no traces because every POST 404s", which is the
    shape of the bug that kept the chassis and ShipGate from ever exporting a
    span. So the failure is counted at the point it happens, and the count is
    itself a metric that has to get out.

    `OTLPSpanExporter` never raises. It catches, logs, retries and returns
    `FAILURE`, so against the real exporter every fault, unreachable, 500 or
    hang alike, arrives here as `REJECTED` and the finer reasons below are
    unreachable. They are kept because this wraps any `SpanExporter` and a
    raising one is permitted by the interface, and they are tested directly
    rather than through OTLP, which cannot produce them.
    """

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception as exc:
            count_export_failure(_reason(exc))
            # Swallowed on purpose. Raising here would surface inside the batch
            # processor's thread, and an exporter that can take down its host is
            # a worse failure than one that loses spans.
            return SpanExportResult.FAILURE

        if result is SpanExportResult.FAILURE:
            count_export_failure(REJECTED)
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)


def _reason(exc: Exception) -> str:
    """A closed set of words, never the exception message.

    This becomes a metric label. An unbounded value would create a time series
    per distinct error string, and a failing endpoint produces plenty of them.
    """
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return TIMEOUT
    if "connection" in name or "connect" in name:
        return CONNECTION
    return UNKNOWN
