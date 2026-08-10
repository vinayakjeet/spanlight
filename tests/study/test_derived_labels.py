from __future__ import annotations

import pytest

from study.derive_labels import classify, upper_fence

CHEAP = 0.00008
FENCE_COST = 0.0001
FENCE_LATENCY = 800.0


def session(*, verdict="pass", error=None, cost=CHEAP, latency=500.0) -> dict:
    item_attributes = {"spanlight.session.id": "abc"}
    if error:
        item_attributes["error.type"] = error
    return {
        "item": {"status": "ERROR" if error else "UNSET", "attributes": item_attributes},
        "model_calls": [
            {
                "duration_ms": latency,
                "attributes": {
                    "shipgate.verdict": verdict,
                    "spanlight.cost_usd_equivalent": cost,
                },
            }
        ],
    }


def test_a_fail_verdict_is_silently_wrong() -> None:
    """Expected and output are the same string in every session, so a fail is a
    wrong score under the harness's own contract rather than a judgement call."""
    assert classify(session(verdict="fail"), FENCE_COST, FENCE_LATENCY)[0] == "A7"


def test_a_clean_session_is_clean() -> None:
    assert classify(session(), FENCE_COST, FENCE_LATENCY)[0] == "A1"


def test_a_parse_failure_is_not_a_provider_failure() -> None:
    """A6 and A2 both arrive as an error on the item span. Collapsing them would
    hide that nothing failed at the transport layer and every status check
    passed, which is the only thing that distinguishes them."""
    parse = classify(session(error="JudgeParseError"), FENCE_COST, FENCE_LATENCY)
    provider = classify(session(error="RateLimitError"), FENCE_COST, FENCE_LATENCY)

    assert parse[0] == "A6"
    assert provider[0] == "A2"


def test_being_wrong_outranks_being_expensive() -> None:
    """A session can match several rules. Ordering decides which label it gets,
    and a wrong answer is the more useful thing to know about a session that also
    cost a fraction of a cent more than its peers."""
    both = session(verdict="fail", cost=FENCE_COST * 2)

    assert classify(both, FENCE_COST, FENCE_LATENCY)[0] == "A7"


def test_an_error_outranks_everything() -> None:
    """A session that failed and was expensive is a failure. Labelling it A4
    would put a crash in the cost table."""
    both = session(error="RateLimitError", cost=FENCE_COST * 2, verdict="fail")

    assert classify(both, FENCE_COST, FENCE_LATENCY)[0] == "A2"


def test_the_outlier_classes_are_reachable() -> None:
    """The ordering above is only defensible if A4 and A5 can still be reached by
    a session that is otherwise clean."""
    expensive = session(cost=FENCE_COST * 2)
    slow = session(latency=FENCE_LATENCY * 2)

    assert classify(expensive, FENCE_COST, FENCE_LATENCY)[0] == "A4"
    assert classify(slow, FENCE_COST, FENCE_LATENCY)[0] == "A5"


def test_the_fence_sits_above_a_symmetric_distribution() -> None:
    """Tukey on a clean spread flags nothing. A rule that fires on ordinary data
    is a rule that would have made every session an outlier."""
    fence = upper_fence([float(n) for n in range(100)])

    assert fence > 99


def test_the_fence_catches_a_planted_outlier() -> None:
    fence = upper_fence([*[float(n) for n in range(100)], 1000.0])

    assert 99.0 < fence < 1000.0


def test_the_fence_degenerates_on_a_distribution_with_no_spread() -> None:
    """Recorded rather than guarded. With no interquartile range the fence
    collapses onto Q3, so on a workload where every session costs exactly the
    same, any session costing a fraction more is an outlier. That does not happen
    on this corpus, where the range is 0.00007184 to 0.00010699, and anyone
    reusing the rule on a cached or fixed-length workload needs to check it
    before trusting the count."""
    assert upper_fence([10.0] * 100) == 10.0


def test_a_session_with_no_model_call_does_not_crash() -> None:
    """A session span can end without a model call: the sampler drops one, or the
    host raised before calling out. The old max() over an empty sequence made
    that a ValueError in the middle of a 500 session run."""
    empty = {"item": {"status": "UNSET", "attributes": {}}, "model_calls": []}

    assert classify(empty, FENCE_COST, FENCE_LATENCY)[0] == "A1"


@pytest.mark.parametrize("verdict", ["pass", "fail"])
def test_the_rule_is_recorded_with_the_label(verdict: str) -> None:
    """Every label ships with the reason it was assigned, so a reader checking one
    session does not have to re-derive the rule from the distribution."""
    _, why = classify(session(verdict=verdict), FENCE_COST, FENCE_LATENCY)

    assert why
