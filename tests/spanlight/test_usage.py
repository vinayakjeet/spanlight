from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight.attributes import (
    COST_USD,
    COST_USD_EQUIVALENT,
    GEN_AI_INPUT_TOKENS,
    GEN_AI_OUTPUT_TOKENS,
)


def test_records_token_counts(spans: InMemorySpanExporter) -> None:
    with spanlight.model_span(provider="groq"):
        spanlight.record_usage(tokens_in=412, tokens_out=88)

    (span,) = spans.get_finished_spans()
    assert span.attributes[GEN_AI_INPUT_TOKENS] == 412
    assert span.attributes[GEN_AI_OUTPUT_TOKENS] == 88


def test_free_tier_spend_is_zero_and_the_equivalent_is_not(
    spans: InMemorySpanExporter,
) -> None:
    """The whole reason there are two cost attributes.

    Every price in `llm/providers/quotas.yaml` is 0.0 because free-tier calls
    genuinely cost nothing. Recording only that leaves the M3.3 ceiling detector
    incapable of ever firing and the M7 cost analysis a table of zeros. Both
    numbers here are true and they must not be conflated.
    """
    with spanlight.model_span(provider="groq"):
        spanlight.record_usage(
            tokens_in=1_000_000, tokens_out=1_000_000, cost_usd=0.0, provider="groq"
        )

    (span,) = spans.get_finished_spans()
    assert span.attributes[COST_USD] == 0.0
    assert span.attributes[COST_USD_EQUIVALENT] == 0.59 + 0.79


def test_an_unknown_provider_gets_no_equivalent(spans: InMemorySpanExporter) -> None:
    """None rather than zero. A zero is indistinguishable from a genuinely free
    provider and would drag a study average toward nothing."""
    with spanlight.model_span(provider="some-new-provider"):
        spanlight.record_usage(tokens_in=100, tokens_out=100, provider="some-new-provider")

    (span,) = spans.get_finished_spans()
    assert COST_USD_EQUIVALENT not in span.attributes


def test_a_provider_with_unpublished_prices_gets_no_equivalent(
    spans: InMemorySpanExporter,
) -> None:
    """Sarvam's prices are null in list_prices.yaml on purpose. An unknown price
    must not become a confident number in a study table."""
    with spanlight.model_span(provider="sarvam"):
        spanlight.record_usage(tokens_in=100, tokens_out=100, provider="sarvam")

    (span,) = spans.get_finished_spans()
    assert COST_USD_EQUIVALENT not in span.attributes


def test_no_equivalent_without_token_counts() -> None:
    assert spanlight.cost_usd_equivalent("groq", None, 88) is None
    assert spanlight.cost_usd_equivalent("groq", 412, None) is None


def test_recording_outside_a_span_is_a_no_op() -> None:
    """Instrumentation must never raise into the host application."""
    spanlight.record_usage(tokens_in=1, tokens_out=1, provider="groq")
