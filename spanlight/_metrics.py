from __future__ import annotations

import functools

from opentelemetry import metrics

from spanlight.attributes import (
    DETECTIONS_TOTAL,
    EXPORT_FAILURES_TOTAL,
    SESSION_COST_USD,
    TOKEN_USAGE,
    TRACER_NAME,
)

_service = "unknown"


def set_service(name: str) -> None:
    """Record the service name for the detection counter's `service` label.

    The resource on the MeterProvider already carries `service.name`, but how a
    resource attribute reaches a Prometheus query depends on the backend's
    conversion rules, and SPEC names `service` as a label on the counter itself.
    Carrying it explicitly means the alert in M6 can be written once and keep
    working if that conversion changes underneath it.
    """
    global _service
    _service = name


@functools.cache
def _counter() -> metrics.Counter:
    """Safe to build before a MeterProvider exists.

    `get_meter` hands back a proxy while none is configured, and instruments made
    on it start forwarding once one is set, so this can be cached at first use
    without pinning a no-op. Verified rather than assumed: see
    `tests/spanlight/test_detection_emission.py`, which configures the provider
    after the counter already exists.
    """
    return metrics.get_meter(TRACER_NAME).create_counter(
        DETECTIONS_TOTAL,
        description="Detections raised by Spanlight, by type.",
    )


def count_detection(detection_type: str) -> None:
    _counter().add(1, {"type": detection_type, "service": _service})


@functools.cache
def _export_failures() -> metrics.Counter:
    return metrics.get_meter(TRACER_NAME).create_counter(
        EXPORT_FAILURES_TOTAL,
        description="Span exports that did not land, by reason.",
    )


@functools.cache
def _session_cost() -> metrics.Histogram:
    return metrics.get_meter(TRACER_NAME).create_histogram(
        SESSION_COST_USD,
        # Deliberately no unit. The OTLP to Prometheus translation appends the
        # unit to the metric name when it is not already a suffix, and this name
        # ends in `_usd` already, so declaring `USD` risks arriving as
        # `spanlight_session_cost_usd_usd` and silently emptying every panel that
        # queries it. `{token}` on the counter below is safe: annotation units in
        # braces are dropped by that translation. The display unit belongs on the
        # panel, which is where the dashboards set it.
        description="Counterfactual cost of one session at published list prices.",
    )


def record_session_cost(usd_equivalent: float | None) -> None:
    """One observation per finished session, and none for a session that spent
    nothing.

    The same number is already a span attribute, deliberately. The attribute
    answers which session cost the most last Tuesday, which needs the identity of
    a run and so cannot be a metric label without a time series per session. The
    histogram answers what a session costs across a fleet, which needs
    aggregation the trace store cannot do cheaply.

    Sessions with no priced model call are skipped rather than recorded as zero.
    A session that opened and closed without calling anything is not a cheap
    session, and counting it as one pulls every quantile toward zero.
    """
    if not usd_equivalent:
        return
    _session_cost().record(usd_equivalent, {"service": _service})


@functools.cache
def _token_usage() -> metrics.Histogram:
    return metrics.get_meter(TRACER_NAME).create_histogram(
        TOKEN_USAGE,
        unit="{token}",
        description="Tokens per model call, split by direction.",
    )


def record_token_usage(system: str, direction: str, tokens: int | None) -> None:
    """Input and output are recorded separately because they are priced
    separately, so a single total cannot be turned back into a cost."""
    if tokens is None:
        return
    _token_usage().record(
        tokens, {"gen_ai.system": system, "type": direction, "service": _service}
    )


def count_export_failure(reason: str) -> None:
    """The only signal that tracing itself has stopped working.

    `reason` is a short closed set of words, never an exception message. This is
    a metric label, so an unbounded value would multiply the time series by every
    distinct error string a failing endpoint can produce, which is how a
    monitoring bill and a Prometheus instance both fall over.
    """
    _export_failures().add(1, {"reason": reason, "service": _service})
