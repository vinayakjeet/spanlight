from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import spanlight
from llm import ChatClient, ChatMessage
from llm.types import RateLimitError
from spanlight.attributes import (
    ATTEMPT_NUMBER,
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


def model_span(spans: InMemorySpanExporter):
    """The `chat` span, ignoring the attempts beneath it.

    These tests used to unpack a single span, which is how they read before the
    retry loop emitted one span per try. Selecting by name rather than counting
    keeps them asserting what they are about.
    """
    return next(s for s in spans.get_finished_spans() if s.name == "chat")


def attempt_spans(spans: InMemorySpanExporter):
    return [s for s in spans.get_finished_spans() if s.name.startswith("attempt")]


async def test_complete_emits_a_model_span_with_usage(
    spans: InMemorySpanExporter,
) -> None:
    """M4.3. The chassis already logged provider, model, tokens, cost and
    latency at llm/client.py, so this is mapping onto the convention rather
    than inventing anything."""
    response = await ChatClient().complete(
        "mock", [ChatMessage(role="user", content="hello")]
    )

    span = model_span(spans)
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

    span = model_span(spans)
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

    span = model_span(spans)
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

    span = model_span(spans)
    assert attempts == 2
    # Both attempts, not just the one that succeeded.
    assert span.end_time - span.start_time > 40_000_000


async def test_every_attempt_gets_its_own_span(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same design, and the half that was missing.

    Wrapping the loop was right, and it made retries invisible: three attempts
    and one attempt produced the same span, differing only in duration. Measured
    over 500 real sessions, where a retry could not be told from a slow provider
    at all. Now the parent says what the call cost and the children say what it
    took.
    """
    monkeypatch.setattr(asyncio, "sleep", REAL_SLEEP)

    from llm.providers import registry

    provider = registry.get_provider("mock")
    original = provider.chat_completion
    attempts = 0

    async def fail_twice(*args: object, **kwargs: object):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RateLimitError("slow down")
        return await original(*args, **kwargs)

    provider.chat_completion = fail_twice
    try:
        await ChatClient(max_retry_attempts=4).complete(
            "mock", [ChatMessage(role="user", content="hi")]
        )
    finally:
        provider.chat_completion = original

    tries = attempt_spans(spans)
    assert [s.attributes[ATTEMPT_NUMBER] for s in tries] == [1, 2, 3]
    assert [s.status.status_code for s in tries] == [
        StatusCode.ERROR,
        StatusCode.ERROR,
        StatusCode.UNSET,
    ]
    # The class, never the message, on the attempts as well as everywhere else.
    assert tries[0].attributes[ERROR_TYPE] == "RateLimitError"


async def test_a_call_that_did_not_retry_emits_one_attempt(
    spans: InMemorySpanExporter,
) -> None:
    """The common case has to stay cheap. One extra span per model call is the
    price of seeing retries at all, and anything more than that is not."""
    await ChatClient().complete("mock", [ChatMessage(role="user", content="hi")])

    assert len(attempt_spans(spans)) == 1
