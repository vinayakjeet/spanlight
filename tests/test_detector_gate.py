from __future__ import annotations

import copy
import json

import pytest

from eval.detector_gate import BASELINE, compare, measure, replay
from study.analyse import load, sessions


@pytest.fixture(scope="module")
def baseline() -> dict:
    if not BASELINE.exists():
        pytest.skip("no baseline recorded")
    return json.loads(BASELINE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runs() -> dict:
    corpus = sessions(load())
    if not corpus:
        pytest.skip("corpus not collected")
    return corpus


def test_the_gate_passes_against_its_own_baseline(baseline: dict) -> None:
    """The one that fails loudest when a detector's behaviour drifts. It replays
    all 500 sessions through the real chain rather than reading the detections
    the corpus already holds, so it moves when the code does."""
    assert compare(measure(), baseline) == []


def test_replaying_reproduces_what_the_collecting_run_recorded(baseline: dict) -> None:
    """Fidelity is the number with signal here, and it is worth its own test:
    the precision figures sit at zero and cannot fall, so they protect nothing."""
    assert baseline["fidelity"] == 1.0


def test_a_raised_ceiling_turns_the_gate_red(runs: dict, baseline: dict) -> None:
    """The deliberate regression. Moving the ceiling above every session's cost
    silences cost_ceiling, which is exactly the change a tuning commit makes when
    someone is tired of the noise, and the gate has to catch it."""
    silenced = replay(runs, ceiling_usd=1.0)
    current = copy.deepcopy(baseline)
    current["fidelity"] = round(
        sum(1 for sid in runs if silenced[sid] == "cost_ceiling") / len(runs), 4
    )
    current["agreement"]["cost_ceiling"]["fired"] = sum(
        1 for verdict in silenced.values() if "cost_ceiling" in verdict
    )

    failures = compare(current, baseline)

    assert failures
    assert any("fidelity" in line for line in failures)
    assert any("cost_ceiling fired on 0" in line for line in failures)


def test_a_lowered_ceiling_is_a_regression_too(runs: dict, baseline: dict) -> None:
    """Both directions. A detector that starts firing more is as much a
    regression as one that stops, and a rule watching only for scores to fall
    would wave it through."""
    current = copy.deepcopy(baseline)
    current["agreement"]["loop"]["fired"] = 7

    failures = compare(current, baseline)

    assert any("loop fired on 7" in line for line in failures)


def test_an_undefined_score_becoming_a_number_blocks(baseline: dict) -> None:
    """`undefined` means the corpus could not tell. A commit that turns it into a
    number has changed either the detector or what the corpus can support, and
    either way the study's claims need rereading before it merges."""
    current = copy.deepcopy(baseline)
    current["agreement"]["loop"]["precision"] = 1.0

    assert compare(current, baseline)


def test_a_shrunken_corpus_blocks(baseline: dict) -> None:
    """A gate that scores a subset and compares it to a full baseline reports a
    smaller number and blames the detector. Catch the input first."""
    current = copy.deepcopy(baseline)
    current["sessions"] = 400

    assert any("corpus changed" in line for line in compare(current, baseline))


def test_replay_leaves_no_tracer_installed(runs: dict) -> None:
    """The swap in `replay` is restored in a `finally`. Leaving it in place hands
    every later test in the process a provider it did not ask for, and the ones
    asserting tracing is off start failing somewhere unrelated."""
    import spanlight._spans as spans_module

    before = spans_module.get_tracer
    replay(dict(list(runs.items())[:2]), ceiling_usd=0.00005)

    assert spans_module.get_tracer is before
