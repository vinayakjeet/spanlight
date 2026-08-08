from __future__ import annotations

import os

# A test run must never ship spans to the real backend. It burns free-tier
# ingest, and worse, it pollutes the M7 study corpus with traffic that was never
# a real agent run, which would quietly corrupt the one artifact in this project
# that nobody else has.
#
# Set here rather than in a fixture because `app/main.py` builds the app at
# import time, so the first `from app.main import app` would already have
# exported. Environment variables outrank `.env` in pydantic-settings, so an
# empty value wins over a configured endpoint.
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = ""

import pytest  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

import spanlight._spans as spans_module  # noqa: E402
from spanlight._detector_framework import registry  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_detectors() -> None:
    """The detector registry is process-global mutable state.

    A test that registers a detector and forgets to unregister it changes the
    behaviour of every test that runs after it, and the failure surfaces
    somewhere else entirely. Doing it here means no individual test can forget.
    """
    yield
    registry.clear_detectors()
    registry.reset()


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """Capture emitted spans without touching the global tracer provider.

    OpenTelemetry ignores a second `set_tracer_provider` and only logs about it,
    so a suite that installs a global provider starts passing or failing based
    on import order. Patching the single function that resolves the tracer keeps
    every test isolated from every other one.

    Lives here rather than under `tests/spanlight/` because `llm/` and `app/` are
    instrumented too, and their tests need to read the spans they emit.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(spans_module, "get_tracer", lambda: provider.get_tracer("test"))
    return exporter
