"""Detector precision and recall against the hand labels.

    PYTHONPATH=. uv run python study/precision.py

Only runs on sessions that were labelled blind. A detector scored against labels
that were produced while looking at its own verdict is scored against itself.

The interesting part is what this refuses to compute. Two of the three detectors
have no positives here and the classes they map to never occur, so precision and
recall are both undefined: zero over zero, not zero. Reporting a zero would say
the detector is wrong, and reporting a one would say it is perfect, and the truth
is that this corpus cannot tell.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from study.analyse import DETECTION, load, sessions, table, wilson  # noqa: E402
from study.label import LABELS  # noqa: E402

# Which hand label means "this detector should have fired". Fixed here rather
# than inferred, because a mapping chosen after seeing the confusion matrix is
# not a test of anything.
#
# `loop` maps to no class at all. The taxonomy has no loop class because the
# workload cannot loop: a batch scorer calls the judge once per item. That is not
# an oversight to patch, it is the coverage result stated in the units of the
# study.
SHOULD_FIRE = {
    "cost_ceiling": {"A4"},
    "silent_tool_failure": {"A7"},
    "loop": set(),
}


def read_labels() -> dict[str, str]:
    if not LABELS.exists():
        raise SystemExit(
            f"{LABELS} does not exist. Run study/label.py first: precision against "
            "labels that do not exist is precision against nothing."
        )
    with LABELS.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return {r["session_id"]: r["label"] for r in rows if r["label"] != "SKIP"}


def ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "undefined"
    low, high = wilson(numerator, denominator)
    return f"{numerator / denominator:.1%} [{low:.0%}-{high:.0%}]"


def main() -> None:
    labels = read_labels()
    runs = sessions(load())
    scored = {sid: runs[sid] for sid in labels if sid in runs}

    print(f"{len(scored)} labelled sessions scored, {len(runs)} in the corpus")
    if not scored:
        raise SystemExit("no labelled session is present in the corpus")

    rows = []
    for detector, positive_labels in SHOULD_FIRE.items():
        tp = fp = fn = tn = 0
        for sid, record in scored.items():
            fired = any(
                DETECTION in s["attributes"]
                and s["attributes"][DETECTION] == detector
                for s in [record["item"], *record["model_calls"]]
            )
            should = labels[sid] in positive_labels
            if fired and should:
                tp += 1
            elif fired and not should:
                fp += 1
            elif not fired and should:
                fn += 1
            else:
                tn += 1

        note = ""
        if not positive_labels:
            note = "no class maps to it: this workload cannot produce one"
        elif tp + fp == 0 and tp + fn == 0:
            note = "never fired, and the class never occurred"

        rows.append(
            (
                detector,
                f"{tp}/{fp}/{fn}/{tn}",
                ratio(tp, tp + fp),
                ratio(tp, tp + fn),
                note,
            )
        )

    table(
        "Detector performance against blind hand labels",
        rows,
        ("detector", "TP/FP/FN/TN", "precision", "recall", "note"),
    )

    print("\n  'undefined' is the honest reading of zero over zero. A precision of")
    print("  0% would say the detector is wrong and 100% would say it is perfect;")
    print("  what is true is that this corpus cannot distinguish those.")

    distribution = {}
    for label in labels.values():
        distribution[label] = distribution.get(label, 0) + 1
    table(
        "Hand-label distribution",
        [
            (label, count, f"{count / len(labels):.1%}")
            for label, count in sorted(distribution.items())
        ],
        ("class", "sessions", "share"),
    )


if __name__ == "__main__":
    main()
