from __future__ import annotations

import os

import pytest

import spanlight

ENDPOINT = os.environ.get("SPANLIGHT_SMOKE_ENDPOINT")
HEADERS = os.environ.get("SPANLIGHT_SMOKE_HEADERS")

pytestmark = pytest.mark.skipif(
    not ENDPOINT,
    reason="export smoke test is opt-in: set SPANLIGHT_SMOKE_ENDPOINT to run it",
)


@pytest.mark.integration
def test_a_span_reaches_the_configured_backend() -> None:
    """M0.1's acceptance check, automated as far as it honestly can be.

    Deliberately opt-in through its own variables rather than reading the
    standard OTEL ones, so that running the suite never sends real traffic. See
    `tests/conftest.py`.

    A successful export is not proof the span is queryable in Tempo, so the
    trace id is printed and the Demo Checkpoint is closed by finding it by hand.
    From inside this process an export that 404s and a span that never arrives
    look the same, which is the whole reason this project exists.
    """
    assert spanlight.init("spanlight-smoke", endpoint=ENDPOINT, headers=HEADERS) is True

    with spanlight.model_span(provider="mock", model="mock-echo") as span:
        trace_id = format(span.get_span_context().trace_id, "032x")

    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    assert provider.force_flush(timeout_millis=10_000) is True

    print(f"\nfind this trace in Tempo: {trace_id}")
