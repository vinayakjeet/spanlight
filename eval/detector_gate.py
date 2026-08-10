"""Block a merge that changes what the detectors do to the field corpus.

    uv run python eval/detector_gate.py
    uv run python eval/detector_gate.py --update-baseline

Applies ShipGate's consumer contract: a fixed dataset, a runner with no
run-to-run variance, a checked-in baseline, and any drop blocks. It does not
import ShipGate. Its gate resolves baselines from a database and would make this
repo's CI fail whenever a sibling repo's main branch broke, which is a larger
coupling than the twenty lines of exact matching it would save.

The corpus is replayed through the real detector chain rather than read for the
`spanlight.detection` attributes already in it. Reading them would compare a
frozen file against itself and pass forever: ShipGate shipped a gate that could
not fail through two separate bugs while twenty-seven unit tests stayed green,
and the shape of that mistake is a check whose inputs cannot move.

Two things are scored, because they fail differently:

- **fidelity**, whether today's code reproduces what the collecting run recorded,
  session by session. This is the one with signal. It sits at 100% and any drop
  is a change in behaviour.
- **agreement**, precision and recall against `study/labels_derived.jsonl`. These
  are the M7 numbers. Some of them sit at a floor and cannot drop, which the
  report says out loud rather than counting as protection.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

import spanlight  # noqa: E402
import spanlight._spans as spans_module  # noqa: E402
from spanlight._detector_framework import SESSION, registry  # noqa: E402
from spanlight._detectors import (  # noqa: E402
    cost_ceiling_detector,
    loop_detector,
    silent_tool_failure_detector,
    watch_for_silent_failure,
)
from spanlight.attributes import DETECTION  # noqa: E402
from study.analyse import COST_EQUIVALENT, DETECTORS, load, sessions  # noqa: E402
from study.derive_labels import DERIVED  # noqa: E402
from study.precision import SHOULD_FIRE  # noqa: E402

BASELINE = pathlib.Path(__file__).parent / "detector_baseline.json"
MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "study" / "corpus_manifest.json"

QUIET = "quiet"


def replay(runs: dict[str, dict], ceiling_usd: float) -> dict[str, str]:
    """Rebuild every session through the real API and record what fires.

    Rebuilt rather than deserialised: a detector reads spans as the SDK produces
    them, so feeding it dictionaries would exercise a parser this library does
    not have and leave the code that runs in production untested.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Restored in the `finally`. Leaving the swap in place hands every later test
    # in the process this provider, and the ones asserting tracing is off start
    # failing somewhere unrelated.
    original_tracer = spans_module.get_tracer
    spans_module.get_tracer = lambda: provider.get_tracer("detector-gate")

    registry.clear_detectors()
    registry.register(loop_detector)
    registry.register(watch_for_silent_failure)
    registry.register(silent_tool_failure_detector, phase=SESSION)
    registry.register(cost_ceiling_detector(ceiling_usd))

    fired: dict[str, str] = {}
    try:
        for session_id, record in sorted(runs.items()):
            exporter.clear()
            with spanlight.session():
                for call in record["model_calls"]:
                    attributes = call["attributes"]
                    with spanlight.model_span(provider=attributes["gen_ai.system"]):
                        spanlight.record_usage(
                            tokens_in=attributes["gen_ai.usage.input_tokens"],
                            tokens_out=attributes["gen_ai.usage.output_tokens"],
                            cost_usd=attributes.get("spanlight.cost_usd", 0.0),
                            provider=attributes["gen_ai.system"],
                        )
            detections = {
                span.attributes[DETECTION]
                for span in exporter.get_finished_spans()
                if DETECTION in (span.attributes or {})
            }
            fired[session_id] = "+".join(sorted(detections)) or QUIET
    finally:
        spans_module.get_tracer = original_tracer
        registry.clear_detectors()
        registry.reset()
    return fired


def recorded(runs: dict[str, dict]) -> dict[str, str]:
    """What the collecting run wrote, in the same shape as a replay."""
    return {
        session_id: "+".join(sorted(record["detections"])) or QUIET
        for session_id, record in sorted(runs.items())
    }


def agreement(fired: dict[str, str], labels: dict[str, str]) -> dict[str, dict]:
    """Precision and recall per detector against the derived labels."""
    scores: dict[str, dict] = {}
    for detector in DETECTORS:
        positives = SHOULD_FIRE[detector]
        tp = fp = fn = 0
        for session_id, label in labels.items():
            if session_id not in fired:
                continue
            did = detector in fired[session_id].split("+")
            should = label in positives
            tp += did and should
            fp += did and not should
            fn += (not did) and should
        scores[detector] = {
            "fired": tp + fp,
            "precision": None if tp + fp == 0 else round(tp / (tp + fp), 4),
            "recall": None if tp + fn == 0 else round(tp / (tp + fn), 4),
        }
    return scores


def measure() -> dict:
    runs = sessions(load())
    ceiling = json.loads(MANIFEST.read_text(encoding="utf-8"))["ceiling_usd"]
    labels = {
        json.loads(line)["session_id"]: json.loads(line)["label"]
        for line in DERIVED.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    fired = replay(runs, ceiling)
    was = recorded(runs)
    matched = sum(1 for session_id in was if fired.get(session_id) == was[session_id])

    return {
        "sessions": len(runs),
        "ceiling_usd": ceiling,
        "fidelity": round(matched / len(runs), 4),
        "agreement": agreement(fired, labels),
        "total_cost_equivalent": round(
            sum(
                c["attributes"].get(COST_EQUIVALENT, 0.0)
                for r in runs.values()
                for c in r["model_calls"]
            ),
            10,
        ),
    }


def compare(current: dict, baseline: dict) -> list[str]:
    """Any drop blocks, and so does any new firing.

    Both directions, because the failure this project exists to prevent is a
    detector nobody trusts. A detector that starts firing more is as much a
    regression as one that stops firing, and only one of those is caught by a
    rule that watches scores fall.
    """
    failures = []
    if current["sessions"] != baseline["sessions"]:
        failures.append(
            f"corpus changed: {baseline['sessions']} sessions to {current['sessions']}"
        )
    if current["fidelity"] < baseline["fidelity"]:
        failures.append(
            f"fidelity {current['fidelity']:.1%} below baseline {baseline['fidelity']:.1%}: "
            "the detectors no longer reproduce what the collecting run recorded"
        )

    for detector, scores in current["agreement"].items():
        was = baseline["agreement"].get(detector, {})
        if scores["fired"] != was.get("fired"):
            failures.append(
                f"{detector} fired on {scores['fired']} sessions, baseline {was.get('fired')}"
            )
        for metric in ("precision", "recall"):
            now, before = scores[metric], was.get(metric)
            if before is None or now is None:
                if now != before:
                    failures.append(
                        f"{detector} {metric} became {now}, baseline {before}"
                    )
            elif now < before:
                failures.append(f"{detector} {metric} {now:.1%} below baseline {before:.1%}")
    return failures


def report(current: dict, baseline: dict | None, failures: list[str]) -> None:
    print(f"{current['sessions']} sessions replayed at a ceiling of ${current['ceiling_usd']}")
    print(f"  fidelity against the recorded corpus: {current['fidelity']:.1%}")
    for detector, scores in current["agreement"].items():
        def show(value: float | None) -> str:
            return "undefined" if value is None else f"{value:.1%}"

        print(
            f"  {detector:20} fired {scores['fired']:>3}  "
            f"precision {show(scores['precision']):>9}  recall {show(scores['recall']):>9}"
        )

    # A number already at its floor cannot fall, so a green gate is not evidence
    # that it is protected. Saying so is the whole reason this section exists.
    floored = [
        f"{detector} {metric}"
        for detector, scores in current["agreement"].items()
        for metric in ("precision", "recall")
        if scores[metric] == 0.0
    ]
    if floored:
        print(f"\n  Not protected by this gate, already at zero: {', '.join(floored)}.")
        print("  A rule that blocks on a drop cannot block a metric that cannot drop.")
        print("  Fidelity is what guards these, and it guards behaviour rather than")
        print("  correctness.")

    if baseline is None:
        print(f"\nNo baseline at {BASELINE.name}. Run with --update-baseline to record one.")
        return
    if failures:
        print("\nFAIL")
        for line in failures:
            print(f"  {line}")
    else:
        print("\nPASS, no drop against the baseline.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Record the current numbers as the baseline. Deliberate, never automatic.",
    )
    args = parser.parse_args()

    current = measure()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None

    if args.update_baseline:
        BASELINE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        report(current, None, [])
        print(f"\nBaseline written to {BASELINE.name}.")
        return

    failures = compare(current, baseline) if baseline else []
    report(current, baseline, failures)
    if failures or baseline is None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
