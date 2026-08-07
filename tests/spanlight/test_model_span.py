from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import spanlight
from spanlight.attributes import (
    ERROR_TYPE,
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM,
)


async def test_decorator_on_async_function(spans: InMemorySpanExporter) -> None:
    @spanlight.model(provider="gemini", model="gemini-flash-latest")
    async def call() -> str:
        return "ok"

    assert await call() == "ok"

    (span,) = spans.get_finished_spans()
    assert span.attributes[GEN_AI_SYSTEM] == "gemini"
    assert span.attributes[GEN_AI_REQUEST_MODEL] == "gemini-flash-latest"
    assert span.attributes[GEN_AI_OPERATION_NAME] == "chat"


async def test_decorator_times_the_await_not_the_coroutine(
    spans: InMemorySpanExporter,
) -> None:
    """A sync wrapper around a coroutine function would close the span before
    the call ran, reporting microseconds for every model call ever made."""
    import asyncio

    @spanlight.model(provider="mock", model="mock-echo")
    async def slow() -> None:
        await asyncio.sleep(0.05)

    await slow()

    (span,) = spans.get_finished_spans()
    elapsed_ns = span.end_time - span.start_time
    assert elapsed_ns > 40_000_000


def test_decorator_on_sync_function(spans: InMemorySpanExporter) -> None:
    @spanlight.model(provider="mock", model="mock-echo")
    def call() -> str:
        return "ok"

    assert call() == "ok"
    assert len(spans.get_finished_spans()) == 1


def test_context_manager_without_a_model(spans: InMemorySpanExporter) -> None:
    """The chassis client passes no model and lets the provider choose, so the
    request-model attribute must be absent rather than guessed at."""
    with spanlight.model_span(provider="mock"):
        pass

    (span,) = spans.get_finished_spans()
    assert span.attributes[GEN_AI_SYSTEM] == "mock"
    assert GEN_AI_REQUEST_MODEL not in span.attributes


def test_failure_records_the_class_not_the_message(spans: InMemorySpanExporter) -> None:
    """A failed call that produces an OK span is the exact failure class this
    project exists to make visible, so it gets a test of its own."""

    class RateLimitError(Exception):
        pass

    with (
        pytest.raises(RateLimitError),
        spanlight.model_span(provider="groq", model="llama-3.1-8b-instant"),
    ):
        raise RateLimitError("quota exhausted for user alice@example.com")

    (span,) = spans.get_finished_spans()
    assert span.attributes[ERROR_TYPE] == "RateLimitError"
    assert span.status.status_code is StatusCode.ERROR
    assert "alice@example.com" not in str(span.attributes)
