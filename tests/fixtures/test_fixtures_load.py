from __future__ import annotations

import copy

import pytest

from eval.fixture_replay import check, load, replay

CASES = load()
DETECTORS = {"loop", "cost_ceiling", "silent_tool_failure", "retry_amplification"}


def by_name(name: str) -> dict:
    return next(case for case in CASES if case["name"] == name)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_the_fixture_behaves_as_recorded(case: dict) -> None:
    """M3.0. Each fixture states an outcome and the replay has to produce it.

    These cover what the field corpus cannot. It holds 500 real sessions and not
    one tool span, so replaying it exercises `cost_ceiling` and leaves the other
    three untested against anything but their own unit tests, which construct the
    spans they then assert on.
    """
    assert check(case) is None


def test_every_detector_has_both_a_positive_and_a_negative() -> None:
    """A corpus of failures cannot tell a working detector from one that fires on
    everything, and a corpus of healthy sessions cannot tell it from one that
    never fires. Both are needed per detector or neither proves much."""
    pairs = {(case["detector"], case["expect"]) for case in CASES}

    assert pairs == {(d, e) for d in DETECTORS for e in ("fires", "quiet")}


def test_a_positive_fires_its_own_detector_and_no_other() -> None:
    """Otherwise a fixture passes for the wrong reason. A loop case that also
    crosses a cost ceiling would keep passing after the loop rule broke."""
    for case in CASES:
        if case["expect"] != "fires":
            continue
        assert replay(case) == {case["detector"]}, case["name"]


def test_a_negative_fires_nothing_at_all() -> None:
    """Stronger than the fixture's own contract, which only asks that its own
    detector stays quiet. A healthy session tripping some other rule is still a
    false positive, and it is the kind that gets a dashboard muted."""
    for case in CASES:
        if case["expect"] == "quiet":
            assert replay(case) == set(), case["name"]


def test_the_check_would_catch_a_detector_that_stopped_firing() -> None:
    """The fixtures only prove the detectors work today. If `check` could not
    fail, this file would be decoration, which is the failure mode the project
    keeps finding in its own work."""
    silenced = copy.deepcopy(by_name("loop.positive"))
    silenced["steps"] = silenced["steps"][:1]

    assert check(silenced) is not None


def test_the_check_would_catch_a_detector_that_fired_too_readily() -> None:
    quiet = copy.deepcopy(by_name("loop.negative"))
    quiet["steps"] = [
        {"kind": "tool", "name": "search", "args": {"q": "same"}} for _ in range(3)
    ] + [{"kind": "model"}]

    assert check(quiet) is not None


def test_every_fixture_says_why_it_exists() -> None:
    """A fixture without a stated reason becomes unmaintainable the first time it
    fails: nobody can tell whether the rule regressed or the case was wrong."""
    for case in CASES:
        assert case.get("why"), case["name"]


def test_the_fixtures_were_actually_found() -> None:
    """Guards every parametrized test above, which pass vacuously over an empty
    directory."""
    assert len(CASES) == 8
