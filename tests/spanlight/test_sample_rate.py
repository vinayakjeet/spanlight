from __future__ import annotations

import pytest

from spanlight._setup import SAMPLE_RATE_VAR, _resolve_sample_rate


def test_unset_everywhere_means_1() -> None:
    """Nothing configured samples everything, which is the only default that
    does not silently drop traces from a service that looks fully wired up."""
    assert _resolve_sample_rate(None, {}) == 1.0


def test_the_argument_beats_the_environment() -> None:
    """A host that passes a rate explicitly, as the demo app would through
    pydantic-settings, must not be overridden by a stale variable."""
    assert _resolve_sample_rate(0.5, {SAMPLE_RATE_VAR: "0.1"}) == 0.5


def test_the_environment_variable_is_read_when_no_argument_is_given() -> None:
    assert _resolve_sample_rate(None, {SAMPLE_RATE_VAR: "0.25"}) == 0.25


def test_a_blank_variable_is_not_a_rate_of_zero() -> None:
    assert _resolve_sample_rate(None, {SAMPLE_RATE_VAR: ""}) == 1.0


@pytest.mark.parametrize("raw", ["mostly", "0,1", "1e-4x"])
def test_an_unparseable_rate_fails_loudly(raw: str) -> None:
    """A bad value must stop startup here rather than produce a sampler whose
    decisions nobody can predict."""
    with pytest.raises(ValueError, match=SAMPLE_RATE_VAR):
        _resolve_sample_rate(None, {SAMPLE_RATE_VAR: raw})


@pytest.mark.parametrize("rate", ["0", "-0.5", "1.5", "2"])
def test_a_rate_outside_the_unit_interval_fails_loudly(rate: str) -> None:
    with pytest.raises(ValueError, match="outside"):
        _resolve_sample_rate(None, {SAMPLE_RATE_VAR: rate})


@pytest.mark.parametrize("rate", [0.0, -1.0, 1.01])
def test_the_same_bound_holds_for_the_argument(rate: float) -> None:
    with pytest.raises(ValueError, match="outside"):
        _resolve_sample_rate(rate, {})
