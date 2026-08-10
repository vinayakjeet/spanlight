from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import spanlight
import spanlight._metrics as metrics_module
from spanlight.attributes import SESSION_COST_USD, TOKEN_USAGE


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    monkeypatch.setattr(
        metrics_module, "_session_cost", lambda: meter.create_histogram(SESSION_COST_USD)
    )
    monkeypatch.setattr(
        metrics_module, "_token_usage", lambda: meter.create_histogram(TOKEN_USAGE)
    )
    return reader


def points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def priced_call(tokens_in: int, tokens_out: int) -> None:
    with spanlight.model_span(provider="groq"):
        spanlight.record_usage(
            tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=0.0, provider="groq"
        )


def test_a_session_records_one_cost_observation(
    spans, recorded: InMemoryMetricReader
) -> None:  # noqa: ARG001
    """One per session, not one per model call. The histogram's unit is a
    session, and recording per call would make its quantiles a statement about
    call size while reading like a statement about run cost."""
    with spanlight.session():
        priced_call(400, 100)
        priced_call(400, 100)

    observations = points(recorded, SESSION_COST_USD)

    assert len(observations) == 1
    assert observations[0].count == 1


def test_the_observation_is_the_sum_of_the_session_s_calls(
    spans, recorded: InMemoryMetricReader
) -> None:  # noqa: ARG001
    one = spanlight.cost_usd_equivalent("groq", 400, 100)
    with spanlight.session():
        priced_call(400, 100)
        priced_call(400, 100)

    assert points(recorded, SESSION_COST_USD)[0].sum == pytest.approx(2 * one)


def test_a_session_that_spent_nothing_records_nothing(
    spans, recorded: InMemoryMetricReader
) -> None:  # noqa: ARG001
    """A run that opened and closed without a priced call is not a cheap run.
    Recording it as zero drags every quantile toward zero, and the p50 of a
    fleet then measures how many sessions never called a model."""
    with spanlight.session(), spanlight.tool_span("search", args={"q": "x"}):
        pass

    assert points(recorded, SESSION_COST_USD) == []


def test_an_unpriced_provider_records_nothing(
    spans, recorded: InMemoryMetricReader
) -> None:  # noqa: ARG001
    """`cost_usd_equivalent` returns None rather than 0.0 for a provider with no
    published price, and None must stay out of the histogram for the same reason
    it stays off the span."""
    with spanlight.session(), spanlight.model_span(provider="nonesuch"):
        spanlight.record_usage(
            tokens_in=400, tokens_out=100, cost_usd=0.0, provider="nonesuch"
        )

    assert points(recorded, SESSION_COST_USD) == []


def test_a_failed_session_still_records_what_it_spent(
    spans, recorded: InMemoryMetricReader
) -> None:  # noqa: ARG001
    """The expensive run that crashed is the one worth seeing in the
    distribution, so the observation goes out on the way past the exception."""

    class Boom(Exception):
        pass

    with pytest.raises(Boom), spanlight.session():
        priced_call(400, 100)
        raise Boom

    assert len(points(recorded, SESSION_COST_USD)) == 1


def test_tokens_are_recorded_by_direction(
    spans, recorded: InMemoryMetricReader
) -> None:  # noqa: ARG001
    """Input and output are priced differently, so a single total cannot be
    turned back into a cost."""
    with spanlight.session():
        priced_call(412, 88)

    by_direction = {
        point.attributes["type"]: point.sum for point in points(recorded, TOKEN_USAGE)
    }

    assert by_direction == {"input": 412, "output": 88}


def test_token_usage_carries_the_provider(
    spans, recorded: InMemoryMetricReader
) -> None:  # noqa: ARG001
    with spanlight.session():
        priced_call(412, 88)

    assert {p.attributes["gen_ai.system"] for p in points(recorded, TOKEN_USAGE)} == {
        "groq"
    }
