from __future__ import annotations

import asyncio
import time

import structlog

import spanlight
from llm.providers.registry import get_provider
from llm.retry import retry_with_backoff
from llm.throttle import InMemoryThrottle, ThrottleBackend
from llm.types import ChatMessage, ChatResponse, RateLimitError
from spanlight.attributes import GEN_AI_RESPONSE_MODEL

logger = structlog.get_logger(__name__)


class ChatClient:
    """Provider-agnostic chat-completion client.

    Call flow: throttle gate (queue behind an active 429 cooldown) -> retry with
    exponential backoff + jitter (transient errors only) -> provider HTTP call ->
    structured cost/token/latency log.
    """

    def __init__(
        self,
        throttle: ThrottleBackend | None = None,
        max_retry_attempts: int = 5,
    ) -> None:
        self._throttle = throttle or InMemoryThrottle()
        self._max_retry_attempts = max_retry_attempts

    async def complete(
        self, provider: str, messages: list[ChatMessage], **kwargs: object
    ) -> ChatResponse:
        provider_impl = get_provider(provider)

        # Counts tries across the whole retry loop. Tenacity re-invokes the
        # function rather than passing a number in, so the count has to live
        # outside it. A list because a closure cannot rebind an int.
        tries = [0]

        @retry_with_backoff(max_attempts=self._max_retry_attempts)
        async def _attempt() -> ChatResponse:
            tries[0] += 1
            # The gate is checked inside the retry loop, not once before it.
            # A 429 trips the throttle with the delay the provider asked for, and
            # only a gate inside the loop makes the next attempt honour it.
            # Checking once outside meant retries fell back to exponential
            # backoff, which caps well below what a provider can ask for: a real
            # Gemini 429 requested 40 seconds while five attempts of backoff
            # totalled about 31, so every retry was spent while still rate
            # limited and the call failed with quota to spare.
            wait = await self._throttle.is_open(provider)
            if wait > 0:
                await asyncio.sleep(wait)

            # Opened after the throttle sleep, so an attempt's duration is the
            # request rather than the queueing in front of it. The wait is still
            # in the parent, which is where a reader looks for what the call
            # cost in wall clock.
            with spanlight.attempt_span(tries[0]):
                try:
                    return await provider_impl.chat_completion(messages, **kwargs)
                except RateLimitError as exc:
                    await self._throttle.trip(provider, exc.retry_after)
                    raise

        # The span wraps the retry loop rather than each attempt, so its duration
        # is what the caller actually waited, throttle sleeps and backoff
        # included. A span per attempt would report the last one and make a call
        # that spent forty seconds rate limited look fast.
        with spanlight.model_span(provider=provider) as span:
            start = time.monotonic()
            response = await _attempt()
            response.latency_ms = (time.monotonic() - start) * 1000

            span.set_attribute(GEN_AI_RESPONSE_MODEL, response.model)
            spanlight.record_usage(
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                cost_usd=response.cost_usd,
                provider=response.provider,
            )

        # Stays. The span goes to Grafana and the log goes to the container's
        # stdout, and the two get read in different situations by different
        # people. Deleting it to avoid duplication would trade a line that costs
        # nothing for a gap when tracing is switched off.
        logger.info(
            "llm.call",
            provider=response.provider,
            model=response.model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
        )
        return response
