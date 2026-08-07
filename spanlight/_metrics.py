from __future__ import annotations

import functools

from opentelemetry import metrics

from spanlight.attributes import DETECTIONS_TOTAL, TRACER_NAME

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
