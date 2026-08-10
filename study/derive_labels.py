"""Assign a taxonomy class to every session by rule, and say which rules cannot.

    uv run python study/derive_labels.py

The pre-registered plan was to hand-label a sample of 100 sessions blind. Trace 3
broke the premise. 35.8% of this corpus is class A7, a verdict that was produced
and is wrong, and nothing in a span distinguishes one from a correct verdict, so
a labeller working honestly from traces calls every one of them A1. Hand-labelling
would have measured what a trace can show and published it as what happened.

What is left once that is faced is that every class this corpus contains is either
derivable from the corpus or invisible in it, and neither case is improved by a
human reading spans. So the rules live here, in code, where they are stated once
and applied to all 500 sessions rather than to a sample. `study/label.py` keeps
its original job and becomes the audit: a human labelling blind, compared against
these labels, measures the gap between what happened and what a trace shows.

Deviates from the pre-registered method. `study/threats.md` records the deviation
and the reason, which is the only honest way to change a plan after seeing data.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from study.analyse import (  # noqa: E402
    COST_EQUIVALENT,
    ERROR_TYPE,
    VERDICT,
    load,
    sessions,
    table,
)

DERIVED = pathlib.Path(__file__).parent / "labels_derived.jsonl"

# Tukey's rule, computed from this corpus rather than chosen for it. The taxonomy
# fixed "the threshold is set from the corpus, not guessed" in advance and left
# the rule open; anything tuned until the count looked right would be the same
# mistake ShipGate made setting a gate threshold by intuition.
IQR_MULTIPLIER = 1.5


def upper_fence(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    q1 = statistics.median(ordered[: n // 2])
    q3 = statistics.median(ordered[(n + 1) // 2 :])
    return q3 + IQR_MULTIPLIER * (q3 - q1)


def classify(record: dict, cost_fence: float, latency_fence: float) -> tuple[str, str]:
    """One label per session, in this order.

    The taxonomy says first match wins and lists A1 first, which cannot be the
    order it is applied in: A1 would absorb everything. Specific classes go
    before the clean one, and A7 goes before the resource classes, because a
    wrong answer is the more useful thing to know about a session that is also
    slightly expensive.
    """
    item = record["item"]
    error = item["attributes"].get(ERROR_TYPE)
    if error == "JudgeParseError":
        return "A6", "the reply did not parse"
    if error:
        return "A2", f"failed and surfaced as {error}"

    # Expected and output are the same string in all 500 sessions: the dataset
    # labels every ticket `billing` and the stub target answers `billing`. So a
    # fail verdict is a wrong score under the harness's own contract, and
    # study/replay_verdicts.json confirms which tickets produce one and that they
    # do it every time.
    if any(c["attributes"].get(VERDICT) == "fail" for c in record["model_calls"]):
        return "A7", "verdict fail where expected and output are identical"

    cost = sum(c["attributes"].get(COST_EQUIVALENT, 0.0) for c in record["model_calls"])
    if cost > cost_fence:
        return "A4", f"cost {cost:.8f} above the fence"

    latency = max((c["duration_ms"] for c in record["model_calls"]), default=0.0)
    if latency > latency_fence:
        return "A5", f"model call {latency:.1f}ms above the fence"

    return "A1", "no rule matched"


def main() -> None:
    runs = sessions(load())

    costs = [
        sum(c["attributes"].get(COST_EQUIVALENT, 0.0) for c in r["model_calls"])
        for r in runs.values()
    ]
    # Latency on the model span. The session span is half queue, so fencing on it
    # would elect the fourth item of every batch a latency outlier.
    latencies = [
        max((c["duration_ms"] for c in r["model_calls"]), default=0.0)
        for r in runs.values()
    ]
    cost_fence = upper_fence(costs)
    latency_fence = upper_fence(latencies)

    print(f"{len(runs)} sessions")
    print(f"  A4 above {cost_fence:.8f} USD equivalent")
    print(f"  A5 above {latency_fence:.1f}ms on the model span")

    counts: dict[str, int] = {}
    with DERIVED.open("w", encoding="utf-8") as fh:
        for session_id, record in sorted(runs.items()):
            label, why = classify(record, cost_fence, latency_fence)
            counts[label] = counts.get(label, 0) + 1
            fh.write(
                json.dumps(
                    {
                        "session_id": session_id,
                        "label": label,
                        "source": "derived",
                        "rule": why,
                    }
                )
                + "\n"
            )

    table(
        "Derived label distribution",
        [
            (label, counts.get(label, 0), f"{counts.get(label, 0) / len(runs):.1%}")
            for label in ("A1", "A2", "A4", "A5", "A6", "A7")
        ],
        ("class", "sessions", "share"),
    )

    # First match wins, so a session that is both wrong and expensive is counted
    # once, as A7. Without this the A4 row reads as "no session cost more than
    # its peers", when what happened is that every session which did was also
    # wrong.
    over_cost = [
        sid
        for sid, r in runs.items()
        if sum(c["attributes"].get(COST_EQUIVALENT, 0.0) for c in r["model_calls"])
        > cost_fence
    ]
    over_latency = [
        sid
        for sid, r in runs.items()
        if max((c["duration_ms"] for c in r["model_calls"]), default=0.0) > latency_fence
    ]
    labels = {}
    for sid, record in runs.items():
        labels[sid] = classify(record, cost_fence, latency_fence)[0]
    table(
        "Sessions matching a resource rule, before first-match-wins",
        [
            (
                "above the cost fence",
                len(over_cost),
                sum(1 for sid in over_cost if labels[sid] == "A7"),
            ),
            (
                "above the latency fence",
                len(over_latency),
                sum(1 for sid in over_latency if labels[sid] == "A7"),
            ),
        ],
        ("rule", "sessions", "also A7"),
    )
    print("\n  Every session above the cost fence is a wrong verdict, and this is")
    print("  not a coincidence: the judge writes a longer reason when it disagrees,")
    print("  so a fail costs a median 38 output tokens against 21 for a pass. Cost")
    print("  is a proxy for disagreement here, which is worth knowing and worth")
    print("  distrusting. It is a property of a judge that explains itself, not a")
    print("  property of expensive sessions, and it would not survive a workload")
    print("  where being wrong is cheap.")

    # Reported rather than silently omitted, because a class missing from a table
    # reads as a class with no instances.
    print("\n  A3, retry absorbed by a later attempt: NOT DERIVABLE, count unknown.")
    print("    The model span wraps ChatClient.complete(), which retries inside")
    print("    itself, so three attempts and one attempt produce the same span and")
    print("    differ only in duration. Neither this rule nor a human reading the")
    print("    trace can separate them. Not zero: unmeasured.")
    print(f"\n  {DERIVED.name} written, {len(runs)} sessions.")


if __name__ == "__main__":
    main()
