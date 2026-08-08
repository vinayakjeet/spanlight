from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from tests.spanlight.collector import collector

REPO_ROOT = Path(__file__).resolve().parents[2]

# Instruments the library the OTLP exporter transports over, which is an
# ordinary thing for a consumer to do, and then traces a run. If exporting a
# span were itself traced, that span would be exported, and so on.
PROBE = """
from opentelemetry.instrumentation.requests import RequestsInstrumentor
RequestsInstrumentor().instrument()

import spanlight
assert spanlight.init("recursion-probe", endpoint="{endpoint}")

with spanlight.session():
    with spanlight.model_span(provider="probe"):
        pass
    with spanlight.tool_span("probe", args={{"q": "x"}}):
        pass

from opentelemetry import trace
assert trace.get_tracer_provider().force_flush(timeout_millis=5_000)
"""


def test_exporting_a_span_does_not_produce_more_spans() -> None:
    """M4.4. Spanlight instruments the demo agent and the demo agent is what
    exercises Spanlight, so the library has to survive being pointed at itself.

    The concrete risk is not hypothetical: the OTLP HTTP exporter transports over
    `requests`, and `opentelemetry-instrumentation-requests` is a normal thing
    for an adopting service to install. Without suppression, one span becomes an
    export, which becomes a span, which becomes an export.

    The SDK already suppresses instrumentation around export, so this pins
    behaviour Spanlight depends on rather than a workaround it adds. That is
    worth a test precisely because it is someone else's guarantee: if it stops
    holding, the symptom is a service that floods its own quota, and the cause
    would be nowhere near the change that caused it.

    Runs in a subprocess because `init()` sets the global tracer provider, which
    is write-once and would leak into the rest of the suite.
    """
    with collector() as (server, endpoint):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(PROBE.format(endpoint=endpoint))],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

        # A runaway loop keeps posting after the process is gone, so give it a
        # moment to misbehave rather than reading the count the instant it exits.
        time.sleep(1.0)
        posts = list(server.paths)

    assert posts, "nothing was exported, so this proves nothing about recursion"
    # One batch for the three spans the probe created. The bound is what matters,
    # not the exact figure: recursion would be unbounded, not off by one.
    assert len(posts) < 5, f"export appears to feed itself: {len(posts)} requests"
    assert set(posts) == {"/v1/traces"}
