from __future__ import annotations

import atexit
import os
from urllib.parse import unquote

import structlog

from spanlight.attributes import SEMCONV_VERSION, SEMCONV_VERSION_ATTRIBUTE

logger = structlog.get_logger(__name__)


TRACES_PATH = "/v1/traces"
METRICS_PATH = "/v1/metrics"


def init(
    service: str,
    endpoint: str | None = None,
    headers: str | None = None,
    sample_rate: float = 1.0,
    cost_ceiling_usd: float | None = None,
) -> bool:
    """Export OTLP spans when an endpoint is configured, otherwise do nothing.

    Returns whether tracing was enabled. Callers report it, because a deliberate
    no-op and a silent failure look identical from the outside and telling them
    apart is the entire point of this project.

    `endpoint` and `headers` fall back to the standard OTEL environment
    variables. They are arguments as well because the app reads its config
    through pydantic-settings from a `.env` file, which populates a settings
    object rather than `os.environ`.

    `sample_rate` samples whole sessions, since a session is one trace and a
    trace-id ratio therefore decides once per run. It defaults to 1.0: a service
    that has gone to the trouble of configuring an endpoint and then silently
    drops nine tenths of its traces is the same "looks configured, exports
    nothing" failure this library exists to catch. SPEC S8's exemption for
    sessions carrying a detection is not implemented, because a head sampler
    decides before a detection can be known, so lowering this drops detected
    sessions along with the rest.

    The SDK is imported inside the enabled branch. A service that has not
    configured Grafana Cloud should not pay to construct an exporter and a batch
    processor it will never use, and `tests/spanlight/test_init.py` asserts the
    import does not happen rather than trusting this comment.
    """
    endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    headers = headers or os.environ.get("OTEL_EXPORTER_OTLP_HEADERS")
    if not endpoint:
        logger.info("spanlight.disabled", reason="OTEL_EXPORTER_OTLP_ENDPOINT not set")
        return False

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    from spanlight._metrics import set_service

    headers_map = _parse_headers(headers)
    traces_endpoint = _signal_endpoint(endpoint, TRACES_PATH)
    metrics_endpoint = _signal_endpoint(endpoint, METRICS_PATH)

    resource = Resource.create(
        {"service.name": service, SEMCONV_VERSION_ATTRIBUTE: SEMCONV_VERSION}
    )
    # Session sampling with no custom sampler. A session is one trace, so a
    # trace-id ratio already decides once per session, and `ParentBased` makes
    # every step inherit the root's verdict, which is S8's requirement that a
    # session is dropped whole rather than halved. The hand-rolled sampler this
    # replaces kept its own unbounded decision cache and had a detection
    # override that could never fire.
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(root=TraceIdRatioBased(sample_rate)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=traces_endpoint, headers=headers_map)
        )
    )
    trace.set_tracer_provider(provider)

    # The detection counter from S4 through S6. An alert needs something it can
    # evaluate on a schedule, and alerting on the presence of a span means
    # querying traces on a timer, which the free tier will not carry.
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=metrics_endpoint, headers=headers_map)
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)
    set_service(service)

    _register_default_detectors(cost_ceiling_usd)

    # Flush even if the process exits before either exporter's timer fires.
    # Short-lived CLI runs and failing gate jobs are the common case, and the
    # trace of a failing gate is the one worth keeping.
    atexit.register(lambda: provider.force_flush(timeout_millis=5_000))
    atexit.register(lambda: meter_provider.force_flush(timeout_millis=5_000))

    logger.info("spanlight.enabled", service=service, endpoint=traces_endpoint)
    return True


def _register_default_detectors(cost_ceiling_usd: float | None) -> None:
    """Wire the detectors up once tracing is on.

    Registered here rather than at import so that a service which never
    configures an endpoint pays nothing for detectors whose findings it has
    nowhere to send.

    The cost ceiling has no default. Any number invented here would end up
    quoted as a threshold somebody measured, and a ceiling is only meaningful
    against a known workload. Omit it and the other two detectors still run.
    """
    from spanlight._detector_framework import SESSION, registry
    from spanlight._detectors import (
        cost_ceiling_detector,
        loop_detector,
        silent_tool_failure_detector,
        watch_for_silent_failure,
    )

    registry.clear_detectors()
    registry.register(loop_detector)
    registry.register(watch_for_silent_failure)
    registry.register(silent_tool_failure_detector, phase=SESSION)
    if cost_ceiling_usd is not None:
        registry.register(cost_ceiling_detector(cost_ceiling_usd))


def _signal_endpoint(endpoint: str, path: str) -> str:
    """Resolve a signal-agnostic OTLP base URL to one signal's endpoint.

    `OTLPSpanExporter(endpoint=...)` treats an explicit endpoint as the complete
    URL for its signal and appends nothing, while the OTLP environment-variable
    spec says a non-signal-specific endpoint gains `/v1/traces`. Grafana Cloud
    hands you the signal-agnostic form (it ends in `/otlp`), so passing it
    through unchanged POSTs every span to a path that does not accept them.

    The chassis `otel_bootstrap` and ShipGate's `tracing.py` both pass the
    endpoint through unchanged, so neither has ever exported a span to Grafana.
    Nothing caught it because an export that 404s and an application with no
    traces look the same from inside the process.

    Takes the signal path as an argument because metrics need the same treatment
    and would otherwise repeat the same mistake at `/v1/metrics`. A caller who
    already resolved the endpoint for one signal gets it rewritten for the other
    rather than ending up at `/v1/traces/v1/metrics`.
    """
    trimmed = endpoint.rstrip("/")
    for known in (TRACES_PATH, METRICS_PATH):
        if trimmed.endswith(known):
            trimmed = trimmed[: -len(known)]
            break
    return trimmed + path


def _parse_headers(raw: str | None) -> dict[str, str]:
    """Parse OTEL_EXPORTER_OTLP_HEADERS: 'key1=value1,key2=value2'.

    Values are percent-encoded in this format and have to be decoded. Grafana
    Cloud issues its header as `Authorization=Basic%20<token>`, so a parser that
    splits without decoding sends the literal `Basic%20<token>` and earns a 401
    that reads like a bad credential rather than a bad parser. The chassis and
    ShipGate both inherit this bug.
    """
    if not raw:
        return {}
    headers = {}
    for pair in raw.split(","):
        if "=" in pair:
            key, _, value = pair.partition("=")
            headers[unquote(key.strip())] = unquote(value.strip())
    return headers
