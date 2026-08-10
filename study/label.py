"""Hand-label a random sample of sessions, without seeing what the detectors said.

    uv run python study/label.py

Blind on purpose. If the labeller can see `spanlight.detection`, the labels drift
toward agreeing with it and the precision number afterwards is measuring how
persuasive the detector was, not whether it was right. Everything the detectors
wrote is stripped before a session is shown, and the stripping is tested.

Resumable. Labels append to `study/labels.jsonl`, so this can be done in sittings
without losing the sample or relabelling anything.
"""

from __future__ import annotations

import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from study.analyse import (  # noqa: E402
    COLD_START,
    COST_EQUIVALENT,
    ERROR_TYPE,
    load,
    sessions,
)

LABELS = pathlib.Path(__file__).parent / "labels.jsonl"
SAMPLE_SIZE = 100

# Fixed in taxonomy.md before collection. Changing it changes which sessions get
# labelled, which is why it is a constant and not an argument.
SAMPLE_SEED = 20260808

CLASSES = {
    "1": ("A1", "clean: verdict produced, no error, normal cost and latency"),
    "2": ("A2", "provider error, surfaced: failed and the failure reached the result"),
    "3": ("A3", "provider error, absorbed by retry: invisible in the result"),
    "4": ("A4", "cost outlier: completed but cost materially more than its peers"),
    "5": ("A5", "latency outlier: completed but took materially longer"),
    "6": ("A6", "malformed reply: answered, did not parse, scored zero"),
    "7": ("A7", "silently wrong: a verdict was produced and it is wrong"),
    "s": ("SKIP", "cannot tell from the trace alone"),
}

HIDDEN_PREFIXES = ("spanlight.detection",)


def blind(span: dict) -> dict:
    """Strip everything a detector wrote.

    Both the attribute and the events, because a detection event's name is the
    verdict spelled out and would give the game away just as completely.
    """
    attributes = {
        k: v
        for k, v in span["attributes"].items()
        if not k.startswith(HIDDEN_PREFIXES)
    }
    events = [e for e in span["events"] if not e["name"].startswith("spanlight.")]
    return {**span, "attributes": attributes, "events": events}


def sample(runs: dict[str, dict]) -> list[str]:
    ordered = sorted(runs)  # sorted first, so the seed alone decides the draw
    rng = random.Random(SAMPLE_SEED)
    return rng.sample(ordered, min(SAMPLE_SIZE, len(ordered)))


def already_labelled() -> dict[str, str]:
    if not LABELS.exists():
        return {}
    with LABELS.open(encoding="utf-8") as fh:
        return {
            json.loads(line)["session_id"]: json.loads(line)["label"]
            for line in fh
            if line.strip()
        }


def show(session_id: str, record: dict, index: int, total: int) -> None:
    item = blind(record["item"])
    calls = [blind(c) for c in record["model_calls"]]
    cost = sum(c["attributes"].get(COST_EQUIVALENT, 0.0) for c in calls)

    print(f"\n{'=' * 70}")
    print(f"session {index}/{total}   {session_id[:16]}")
    print(f"{'=' * 70}")
    print(f"  status        {item['status']}")
    print(f"  duration      {item['duration_ms']:.0f} ms")
    print(f"  cold start    {item['attributes'].get(COLD_START, False)}")
    print(f"  error.type    {item['attributes'].get(ERROR_TYPE, '-')}")
    print(f"  model calls   {len(calls)}")
    print(f"  cost equiv    ${cost:.8f}")
    for n, call in enumerate(calls, 1):
        a = call["attributes"]
        print(
            f"    call {n}: {a.get('gen_ai.response.model', '?')}  "
            f"in={a.get('gen_ai.usage.input_tokens', '?')} "
            f"out={a.get('gen_ai.usage.output_tokens', '?')}  "
            f"{call['duration_ms']:.0f} ms  {call['status']}"
        )
    if item["events"]:
        print(f"  events        {[e['name'] for e in item['events']]}")


def main() -> None:
    runs = sessions(load())
    if len(runs) < SAMPLE_SIZE:
        print(
            f"warning: corpus has {len(runs)} sessions, sampling all of them "
            f"rather than {SAMPLE_SIZE}. Re-run once collection finishes."
        )

    chosen = sample(runs)
    done = already_labelled()
    remaining = [s for s in chosen if s not in done]

    print(f"sample of {len(chosen)} at seed {SAMPLE_SEED}, {len(done)} already labelled")
    if not remaining:
        print("nothing left to label")
        return

    print("\nclasses:")
    for key, (code, description) in CLASSES.items():
        print(f"  {key}  {code:5} {description}")
    print("  q  quit and keep what is labelled so far")

    with LABELS.open("a", encoding="utf-8") as sink:
        for n, session_id in enumerate(remaining, 1):
            show(session_id, runs[session_id], len(done) + n, len(chosen))
            while True:
                choice = input("\n  label> ").strip().lower()
                if choice == "q":
                    print(f"stopped at {len(done) + n - 1}/{len(chosen)}")
                    return
                if choice in CLASSES:
                    code, _ = CLASSES[choice]
                    sink.write(
                        json.dumps(
                            {
                                "session_id": session_id,
                                "label": code,
                                "seed": SAMPLE_SEED,
                            }
                        )
                        + "\n"
                    )
                    sink.flush()
                    break
                print("  not a class. Press a number, s to skip, or q to quit.")


if __name__ == "__main__":
    main()
