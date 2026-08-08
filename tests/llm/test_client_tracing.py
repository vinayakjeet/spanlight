from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import spanlight
from llm import ChatClient, ChatMessage
from llm.types import RateLimitError
from spanlight.attributes import (
    COST_USD,
    ERROR_TYPE,
    GEN_AI_INPUT_TOKENS,
    GEN_AI_OUTPUT_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    SESSION_ID,
)

# Captured at import, before the conftest fixture replaces it per test.
REAL_SLEEP = asyncio.sleep


async def test_complete_emits_a_model_span_with_usage(
    spans: InMemorySpanExporter,
) -> None:
    """M4.3. The chassis already logged provider, model, tokens, cost and
    latency at llm/client.py, so this is mapping onto the convention rather
    than inventing anything."""
    response = await ChatClient().complete(
        "mock", [ChatMessage(role="user", content="hello")]
    )

    (span,) = spans.get_finished_spans()
    assert span.name == "chat"
    assert span.attributes[GEN_AI_SYSTEM] == "mock"
    assert span.attributes[GEN_AI_RESPONSE_MODEL] == response.model
    assert span.attributes[GEN_AI_INPUT_TOKENS] == response.tokens_in
    assert span.attributes[GEN_AI_OUTPUT_TOKENS] == response.tokens_out
    assert span.attributes[COST_USD] == response.cost_usd


async def test_the_span_is_not_told_which_model_to_expect(
    spans: InMemorySpanExporter,
) -> None:
    """The caller passes a provider, not a model, and QUOTAS.md records that
    `gemini-flash-latest` is a moving alias. Recording a requested model here
    would mean writing down a guess."""
    await ChatClient().complete("mock", [ChatMessage(role="user", content="hi")])

    (span,) = spans.get_finished_spans()
    assert GEN_AI_REQUEST_MODEL not in span.attributes
    assert span.attributes[GEN_AI_RESPONSE_MODEL] == "mock-echo"


async def test_the_span_joins_the_surrounding_session(
    spans: InMemorySpanExporter,
) -> None:
    with spanlight.session("gate-run-1"):
        await ChatClient().complete("mock", [ChatMessage(role="user", content="hi")])

    model = next(s for s in spans.get_finished_spans() if s.name == "chat")
    assert model.attributes[SESSION_ID] == "gate-run-1"


async def test_a_provider_failure_records_its_class(spans: InMemorySpanExporter) -> None:
    class Boom(RateLimitError):
        pass

    client = ChatClient(max_retry_attempts=1)

    async def explode(*args: object, **kwargs: object) -> None:
        raise Boom("quota exhausted for alice@example.com")

    from llm.providers import registry

    provider = registry.get_provider("mock")
    original = provider.chat_completion
    provider.chat_completion = explode
    try:
        with pytest.raises(Boom):
            await client.complete("mock", [ChatMessage(role="user", content="hi")])
    finally:
        provider.chat_completion = original

    (span,) = spans.get_finished_spans()
    assert span.attributes[ERROR_TYPE] == "Boom"
    assert span.status.status_code is StatusCode.ERROR
    assert "alice@example.com" not in str(span.attributes)


async def test_the_span_covers_the_retry_loop_not_one_attempt(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A span per attempt would time the last one, so a call that spent forty
    seconds waiting out a 429 would be recorded as fast. QUOTAS.md has a real
    Gemini 429 asking for exactly that.

    `tests/llm/conftest.py` makes `asyncio.sleep` a no-op so backoff does not
    slow the suite down, which would leave this test measuring nothing. Its
    docstring invites exactly this override.
    """
    monkeypatch.setattr(asyncio, "sleep", REAL_SLEEP)

    from llm.providers import registry

    provider = registry.get_provider("mock")
    original = provider.chat_completion
    attempts = 0

    async def slow_then_fine(*args: object, **kwargs: object):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.02)
        if attempts == 1:
            raise RateLimitError("slow down")
        return await original(*args, **kwargs)

    provider.chat_completion = slow_then_fine
    try:
        await ChatClient(max_retry_attempts=3).complete(
            "mock", [ChatMessage(role="user", content="hi")]
        )
    finally:
        provider.chat_completion = original

    (span,) = spans.get_finished_spans()
    assert attempts == 2
    # Both attempts, not just the one that succeeded.
    assert span.end_time - span.start_time > 40_000_000
