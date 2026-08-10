from __future__ import annotations

from pathlib import Path

import yaml
from opentelemetry import trace
from pydantic import BaseModel, ValidationError

from spanlight._metrics import record_token_usage
from spanlight.attributes import (
    COST_USD,
    COST_USD_EQUIVALENT,
    GEN_AI_INPUT_TOKENS,
    GEN_AI_OUTPUT_TOKENS,
)

DEFAULT_LIST_PRICES_PATH = Path(__file__).parent / "list_prices.yaml"


class ListPriceError(Exception):
    """`list_prices.yaml` is malformed."""


class ListPrice(BaseModel):
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    source: str
    last_verified: str


def load_list_prices(path: Path = DEFAULT_LIST_PRICES_PATH) -> dict[str, ListPrice]:
    """Load published list prices, failing fast on a malformed entry.

    Same shape as `llm/providers/registry.load_providers`: a broken config
    should crash at import rather than on the first real call, when the cost of
    finding out is a corrupted study corpus.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("providers", {}) or {}

    prices: dict[str, ListPrice] = {}
    for name, entry in entries.items():
        try:
            prices[name] = ListPrice.model_validate(entry)
        except ValidationError as exc:
            raise ListPriceError(f"{path}: invalid entry for provider '{name}': {exc}") from exc
    return prices


_LIST_PRICES = load_list_prices()


def cost_usd_equivalent(
    provider: str, tokens_in: int | None, tokens_out: int | None
) -> float | None:
    """What these tokens would have cost at published list prices, or None.

    None rather than 0.0 whenever the price is unknown. A zero is
    indistinguishable from a genuinely free provider and would silently pull a
    study average toward nothing, which is the difference between an honest
    table and a wrong one.
    """
    price = _LIST_PRICES.get(provider)
    if price is None or tokens_in is None or tokens_out is None:
        return None
    if price.input_price_per_1m is None or price.output_price_per_1m is None:
        return None

    return (tokens_in / 1_000_000) * price.input_price_per_1m + (
        tokens_out / 1_000_000
    ) * price.output_price_per_1m


def record_usage(
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    provider: str | None = None,
) -> None:
    """Attach token counts and cost to the current span.

    Takes `cost_usd` from the caller rather than computing it. The chassis
    client already derives it in `OpenAICompatibleProvider._cost_usd` from
    `quotas.yaml`, and a second implementation would be a second source of truth
    that drifts.
    """
    span = trace.get_current_span()
    if not span.is_recording():
        return

    if tokens_in is not None:
        span.set_attribute(GEN_AI_INPUT_TOKENS, tokens_in)
    if tokens_out is not None:
        span.set_attribute(GEN_AI_OUTPUT_TOKENS, tokens_out)
    if cost_usd is not None:
        span.set_attribute(COST_USD, cost_usd)

    if provider is not None:
        equivalent = cost_usd_equivalent(provider, tokens_in, tokens_out)
        if equivalent is not None:
            span.set_attribute(COST_USD_EQUIVALENT, equivalent)

        record_token_usage(provider, "input", tokens_in)
        record_token_usage(provider, "output", tokens_out)
