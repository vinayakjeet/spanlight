from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from spanlight._setup import METRICS_PATH, TRACES_PATH, _parse_headers, _signal_endpoint

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

PROBE = """
    import sys

    import spanlight

    enabled = spanlight.init(service="probe")
    sdk_imported = "opentelemetry.sdk" in sys.modules
    print(f"{enabled}|{sdk_imported}")
"""


def _probe(endpoint: str | None) -> tuple[bool, bool]:
    """Run `init()` in a fresh interpreter and report (enabled, sdk_imported).

    The SDK-import assertion only means anything in an isolated process. Run
    in-session, any other test that enables tracing leaves `opentelemetry.sdk`
    in `sys.modules`, and this check starts passing or failing for reasons that
    have nothing to do with the code under test. ShipGate lost a day to a gate
    that could not fail while every unit test stayed green, so a test that
    cannot fail is worth paying a subprocess to avoid.
    """
    env = {k: v for k, v in os.environ.items() if k != "OTEL_EXPORTER_OTLP_ENDPOINT"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    if endpoint is not None:
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint

    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(PROBE)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr

    enabled, sdk_imported = result.stdout.strip().splitlines()[-1].split("|")
    return enabled == "True", sdk_imported == "True"


def test_disabled_without_an_endpoint_and_never_imports_the_sdk() -> None:
    enabled, sdk_imported = _probe(endpoint=None)
    assert enabled is False
    assert sdk_imported is False


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        # What Grafana Cloud actually hands you: signal-agnostic, no suffix.
        (
            "https://otlp-gateway-prod-ap-south-1.grafana.net/otlp",
            "https://otlp-gateway-prod-ap-south-1.grafana.net/otlp/v1/traces",
        ),
        # Trailing slash must not produce a doubled separator.
        (
            "https://otlp-gateway-prod-ap-south-1.grafana.net/otlp/",
            "https://otlp-gateway-prod-ap-south-1.grafana.net/otlp/v1/traces",
        ),
        # Already signal-specific: left alone rather than suffixed twice.
        ("http://localhost:4318/v1/traces", "http://localhost:4318/v1/traces"),
    ],
)
def test_signal_agnostic_endpoints_resolve_to_the_traces_path(
    configured: str, expected: str
) -> None:
    """The chassis and ShipGate both pass the configured endpoint straight to
    the exporter, which appends nothing, so every span POSTs to a path that does
    not accept it. A 404ing export and an app with no traces look identical from
    inside the process, which is why this needs a test rather than a comment."""
    assert _signal_endpoint(configured, TRACES_PATH) == expected


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (
            "https://otlp-gateway-prod-ap-south-1.grafana.net/otlp",
            "https://otlp-gateway-prod-ap-south-1.grafana.net/otlp/v1/metrics",
        ),
        # A traces URL rewritten for metrics, rather than suffixed onto itself.
        ("http://localhost:4318/v1/traces", "http://localhost:4318/v1/metrics"),
        ("http://localhost:4318/v1/metrics", "http://localhost:4318/v1/metrics"),
    ],
)
def test_endpoints_resolve_to_the_metrics_path_too(
    configured: str, expected: str
) -> None:
    """The detection counter travels to a different path than the spans do.
    Reusing the already-resolved traces URL without stripping it would post
    metrics to `/v1/traces/v1/metrics`, which is the same class of mistake as
    the one that kept the chassis from ever exporting a span."""
    assert _signal_endpoint(configured, METRICS_PATH) == expected


def test_header_values_are_percent_decoded() -> None:
    """Grafana Cloud issues `Authorization=Basic%20<token>`. A parser that splits
    without decoding sends the literal `Basic%20<token>` and earns a 401 that
    reads like a bad credential rather than a bad parser. That is how this bug
    was found, and it is inherited from the chassis."""
    parsed = _parse_headers("Authorization=Basic%20abc123,X-Scope-OrgID=tenant%2Done")

    assert parsed["Authorization"] == "Basic abc123"
    assert parsed["X-Scope-OrgID"] == "tenant-one"


def test_enabled_with_an_endpoint() -> None:
    # Nothing listens on this port. init() only builds the exporter, and the
    # batch processor exports on a background timer, so an unreachable endpoint
    # is enough to prove the enabled path without needing Grafana.
    enabled, sdk_imported = _probe(endpoint="http://127.0.0.1:4318/v1/traces")
    assert enabled is True
    assert sdk_imported is True
