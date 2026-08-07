from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight._spans as spans_module
from spanlight._detector_framework import registry


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
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(spans_module, "get_tracer", lambda: provider.get_tracer("test"))
    return exporter
