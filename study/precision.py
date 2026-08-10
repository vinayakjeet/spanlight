"""Detector precision and recall against the derived labels.

    PYTHONPATH=. uv run python study/derive_labels.py
    PYTHONPATH=. uv run python study/precision.py

Scored against `labels_derived.jsonl`, whose rules are stated in
`study/derive_labels.py` and applied to all 500 sessions. They were written after
the corpus existed but never against a detector's output, which is the property
that matters: a label produced while looking at what fired scores a detector
against itself.

Hand labels in `labels.jsonl` are read too if they exist, and they are not the
ground truth here. They measure something else worth having: how far a human
reading a blinded trace lands from what actually happened.

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
from study.derive_labels import DERIVED  # noqa: E402
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


def read(path: pathlib.Path) -> dict[str, str]:
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return {r["session_id"]: r["label"] for r in rows if r["label"] != "SKIP"}


def read_labels() -> dict[str, str]:
    if not DERIVED.exists():
        raise SystemExit(
            f"{DERIVED} does not exist. Run study/derive_labels.py first: precision "
            "against labels that do not exist is precision against nothing."
        )
    return read(DERIVED)


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
        "Detector performance against the derived labels",
        rows,
        ("detector", "TP/FP/FN/TN", "precision", "recall", "note"),
    )

    print("\n  'undefined' is the honest reading of zero over zero. A precision of")
    print("  0% would say the detector is wrong and 100% would say it is perfect;")
    print("  what is true is that this corpus cannot distinguish those.")

    # cost_ceiling's denominator deserves its own sentence, because a bare 0%
    # blames the detector for a labelling decision. A4 is empty by ordering, not
    # by absence: four sessions cleared the cost fence and every one of them was
    # also a wrong verdict, so A7 claimed them first.
    if "A4" not in set(labels.values()):
        print("\n  cost_ceiling scores 0% precision against an empty class. A4 has no")
        print("  members because first-match-wins gave its four candidates to A7,")
        print("  not because no session cost more than its peers. Read it with the")
        print("  resource-rule table in derive_labels.py beside it: at a ceiling")
        print("  taken from the corpus this detector would have fired four times")
        print("  and been right about something every time, for a reason that has")
        print("  nothing to do with cost.")

    # silent_tool_failure's recall is defined here and it was not before. A7 was
    # assumed absent until study/traces.md found 179 of them, and a detector
    # measured against a class with no members is a detector nobody measured.
    if any(label == "A7" for label in labels.values()):
        print("\n  silent_tool_failure now has a denominator: 179 A7 sessions, and it")
        print("  fired on none of them. That is recall 0%, not undefined, and it is")
        print("  the coverage result in its sharpest form. The class it maps to is")
        print("  the largest failure class in the corpus and it cannot see any of")
        print("  it, because there is no tool span to reason about.")

    distribution = {}
    for label in labels.values():
        distribution[label] = distribution.get(label, 0) + 1
    table(
        "Derived label distribution",
        [
            (label, count, f"{count / len(labels):.1%}")
            for label, count in sorted(distribution.items())
        ],
        ("class", "sessions", "share"),
    )

    if not LABELS.exists():
        print(f"\n  No hand labels at {LABELS.name}, so the audit below is unrun.")
        print("  It is the one thing a human adds here: the rules above are")
        print("  reproducible but nobody has checked they match what a person")
        print("  reading the same trace would say.")
        return

    human = read(LABELS)
    shared = [sid for sid in human if sid in labels]
    agreed = [sid for sid in shared if human[sid] == labels[sid]]
    print(f"\n  Audit: {len(agreed)}/{len(shared)} hand labels match the derived one.")

    disagreements: dict[tuple[str, str], int] = {}
    for sid in shared:
        if human[sid] != labels[sid]:
            key = (human[sid], labels[sid])
            disagreements[key] = disagreements.get(key, 0) + 1
    if disagreements:
        table(
            "Where a human reading a blinded trace lands elsewhere",
            [
                (human_label, derived, count)
                for (human_label, derived), count in sorted(
                    disagreements.items(), key=lambda kv: -kv[1]
                )
            ],
            ("hand", "derived", "sessions"),
        )
        print("\n  A hand label of A1 against a derived A7 is the expected shape and")
        print("  is not the labeller being careless. Nothing in the span says the")
        print("  verdict was wrong, so A1 is the correct reading of the evidence")
        print("  they were given. The size of that cell is the size of the gap")
        print("  between what a trace shows and what happened.")


if __name__ == "__main__":
    main()
