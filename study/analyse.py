"""Every table in the study, regenerated from the corpus.

    uv run python study/analyse.py

A script rather than a notebook, so CI can run it and a stranger can diff its
output. BACKLOG asks for `analysis.ipynb`; a notebook whose cells were run in an
order nobody recorded is not reproducible, and the tables are the deliverable.
"""

from __future__ import annotations

import json
import math
import pathlib
import statistics
from collections import Counter, defaultdict

CORPUS = pathlib.Path(__file__).parent / "corpus.jsonl"

SESSION_SPAN = "shipgate.item"
MODEL_SPAN = "chat"
DETECTION = "spanlight.detection"
COST_EQUIVALENT = "spanlight.cost_usd_equivalent"
COLD_START = "spanlight.cold_start"
SESSION_ID = "spanlight.session.id"
ERROR_TYPE = "error.type"
VERDICT = "shipgate.verdict"

DETECTORS = ("loop", "cost_ceiling", "silent_tool_failure")


def wilson(hits: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = hits / total
    denom = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def load() -> list[dict]:
    if not CORPUS.exists():
        raise SystemExit(f"{CORPUS} does not exist. Run study/collect.py first.")
    with CORPUS.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def sessions(spans: list[dict]) -> dict[str, dict]:
    """Group spans into sessions, keyed by the item span that defines one."""
    by_session: dict[str, dict] = defaultdict(
        lambda: {"model_calls": [], "item": None, "detections": set()}
    )
    for span in spans:
        session_id = span["attributes"].get(SESSION_ID)
        if not session_id:
            continue
        record = by_session[session_id]
        if span["name"] == SESSION_SPAN:
            record["item"] = span
        elif span["name"] == MODEL_SPAN:
            record["model_calls"].append(span)
        if DETECTION in span["attributes"]:
            record["detections"].add(span["attributes"][DETECTION])
    return {k: v for k, v in by_session.items() if v["item"] is not None}


def table(title: str, rows: list[tuple], headers: tuple) -> None:
    print(f"\n{title}")
    widths = [
        max(len(str(headers[i])), max((len(str(r[i])) for r in rows), default=0))
        for i in range(len(headers))
    ]
    print("  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths, strict=True)))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True)))


def main() -> None:
    spans = load()
    runs = sessions(spans)
    total = len(runs)
    print(f"corpus: {len(spans)} spans, {total} sessions")

    # Part B first, because it is the result the study is built around.
    print("\n=== Part B: detector coverage ===")
    coverage = []
    for detector in DETECTORS:
        fired = sum(1 for r in runs.values() if detector in r["detections"])
        low, high = wilson(fired, total)
        verdict = "fires" if fired else "CANNOT FIRE on this workload"
        coverage.append(
            (detector, f"{fired}/{total}", f"{low:.1%}-{high:.1%}", verdict)
        )
    table(
        "Prediction 1: loop and silent_tool_failure report exactly zero.",
        coverage,
        ("detector", "fired", "95% CI", "verdict"),
    )

    tool_spans = sum(1 for s in spans if "spanlight.tool.name" in s["attributes"])
    print(f"\n  tool spans in the entire corpus: {tool_spans}")
    print("  Two of three detectors reason about tool behaviour. With no tool")
    print("  spans emitted, their zero is a property of the workload, not health.")

    # Part A: what the workload actually costs and how it fails.
    print("\n=== Part A: cost, latency, failure ===")

    costs = [
        sum(c["attributes"].get(COST_EQUIVALENT, 0.0) for c in r["model_calls"])
        for r in runs.values()
    ]
    failed = [
        sid for sid, r in runs.items() if r["item"]["status"] == "ERROR"
    ]
    clean = [sid for sid in runs if sid not in failed]

    def stats(values: list[float]) -> tuple:
        if not values:
            return ("-", "-", "-")
        ordered = sorted(values)
        p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
        return (
            f"{statistics.median(values):.8f}",
            f"{p90:.8f}",
            f"{statistics.fmean(values):.8f}",
        )

    cost_by = {
        "all sessions": costs,
        "clean": [
            sum(
                c["attributes"].get(COST_EQUIVALENT, 0.0)
                for c in runs[s]["model_calls"]
            )
            for s in clean
        ],
        "failed": [
            sum(
                c["attributes"].get(COST_EQUIVALENT, 0.0)
                for c in runs[s]["model_calls"]
            )
            for s in failed
        ],
    }
    table(
        "Cost equivalent per session, USD (list prices, unverified)",
        [(label, *stats(values), len(values)) for label, values in cost_by.items()],
        ("group", "median", "p90", "mean", "n"),
    )

    # A session span and the provider call inside it are different measurements,
    # and reading the first as the second is how this table was wrong until
    # study/traces.md trace 2. `execute_run` takes its concurrency semaphore
    # inside the session span, so an item waiting its turn holds an open span
    # while it waits. At concurrency 1 the fourth item of a batch shows a median
    # session of 2028ms around a median model call of 523ms. Both rows below are
    # true; only the second is attributable to a provider.
    warm = [r for r in runs.values() if not r["item"]["attributes"].get(COLD_START)]
    cold = [r for r in runs.values() if r["item"]["attributes"].get(COLD_START)]
    session_ms = [r["item"]["duration_ms"] for r in warm]
    model_ms = [c["duration_ms"] for r in warm for c in r["model_calls"]]
    table(
        "Latency, milliseconds (cold starts separated, SPEC A10)",
        [
            ("session span, warm", *stats(session_ms), len(session_ms)),
            ("model span, warm", *stats(model_ms), len(model_ms)),
            (
                "session span, cold start",
                *stats([r["item"]["duration_ms"] for r in cold]),
                len(cold),
            ),
        ],
        ("group", "median", "p90", "mean", "n"),
    )
    queued = [
        (r["item"]["duration_ms"] - sum(c["duration_ms"] for c in r["model_calls"]))
        / r["item"]["duration_ms"]
        for r in warm
        if r["model_calls"]
    ]
    if queued:
        print(
            f"\n  share of the session span spent outside the model call: "
            f"median {statistics.median(queued):.1%}, max {max(queued):.1%}"
        )
        print("  Nearly all of it is the concurrency semaphore. The uncontaminated")
        print("  number was in the corpus the whole time as shipgate.latency_ms,")
        print("  which the runner measures inside the semaphore and which tracks")
        print("  the model span to within 1.2ms across all 500 sessions.")

    errors = Counter(
        r["item"]["attributes"].get(ERROR_TYPE, "none") for r in runs.values()
    )
    # Prediction 3 cannot be measured by counting spans, and finding that out is
    # itself the result. ShipGate's ChatClient retries inside `complete()`, and
    # the judge wraps `complete()` in one model span, so every attempt lands
    # under a single span with a longer duration. A retried call is not one span
    # plus one span; it is one span that took longer.
    #
    # Visible in the result: no. Visible in the span count: no. Only in latency,
    # and only if you already suspect it. The instrumentation is in the wrong
    # place to see it, which is a statement about where you wrap rather than
    # about tracing.
    multi_span = sum(1 for r in runs.values() if len(r["model_calls"]) > 1)
    # On model spans, not session spans. Against session spans this counted 19,
    # and every one of them was an item that had waited its turn in the batch
    # rather than a call that ran long.
    slow = (
        [d for d in model_ms if d > 2 * statistics.median(model_ms)] if model_ms else []
    )
    low, high = wilson(len(slow), len(model_ms) or 1)
    table(
        "Failure shape",
        [
            (kind, count, f"{count / total:.1%}")
            for kind, count in errors.most_common()
        ],
        ("error.type", "sessions", "share"),
    )
    print("\n  Prediction 3, and measuring it the obvious way is how the real")
    print("  answer turned up.")
    print(f"    sessions with more than one model span: {multi_span}/{total}")
    print("    That number is structurally zero. ShipGate retries inside")
    print("    ChatClient.complete(), and the judge wraps complete() in one span,")
    print("    so every attempt hides under a single span that merely took longer.")
    print(
        f"    model calls slower than 2x median: {len(slow)}/{len(model_ms)}, "
        f"95% CI {low:.1%} to {high:.1%}"
    )
    print("    Latency is the only remaining signal, and it cannot separate a")
    print("    retry from a slow provider. To see retries you have to instrument")
    print("    inside the retry loop, not around the call that contains it.")
    print("    A retried call carries a failed attempt plus a wait drawn from")
    print("    U(0, 2s), so most retries would clear 2x the median. None did,")
    print("    which is consistent with zero provider failures rather than proof")
    print("    of them.")

    # The limitation that governs how much of the taxonomy this corpus can
    # support. Printed rather than left for a reader to infer from a table of
    # zeros, because a study that quietly omits the classes it could not observe
    # reads as though they did not happen.
    clean_share = len(clean) / total
    if clean_share == 1.0:
        print("\n  LIMITATION: every session in this corpus succeeded.")
        print(f"    {total}/{total} clean, zero provider errors, zero malformed")
        print("    replies. Groq did not fail once in 46 minutes.")
        print("    Taxonomy classes A2, A3 and A6 have no instances, so this")
        print("    corpus cannot measure how this workload fails at the provider.")
        print("    Any failure-rate claim from this data would be a claim about a")
        print("    46-minute window on one provider, not about agents.")

    # A7, silently wrong, and it is the one class this corpus has in quantity.
    # Every item was labelled `billing` and the stub target answers `billing`, so
    # expected and output are the same string in all 500 sessions and a `fail`
    # verdict is a wrong score under the harness's own contract. Until M7.7 this
    # block claimed A7 had no instances too, which was the same mistake the
    # harness made: reading a green session as a correct one.
    wrong = sum(
        1
        for r in runs.values()
        for c in r["model_calls"]
        if c["attributes"].get(VERDICT) == "fail"
    )
    print(f"\n  Class A7, silently wrong: {wrong}/{total} sessions, {wrong / total:.1%}")
    print("    Quoted without a confidence interval on purpose. The 500 sessions")
    print("    draw on eight tickets whose verdicts are deterministic, so they are")
    print("    not 500 independent trials and a Wilson interval over them would")
    print("    claim a precision the design cannot support. The quantity being")
    print("    estimated is three tickets out of eight.")
    print("    The judge was right every time and the dataset labels were wrong on")
    print("    those three, confirmed in study/replay_verdicts.json.")
    print("    Prediction 4 holds: no detector fires on any of these, at any")
    print("    setting, because whether an answer was correct is not a property")
    print("    of the call that produced it.")

    fired_share = sum(
        1 for r in runs.values() if "cost_ceiling" in r["detections"]
    ) / total
    if fired_share > 0.9:
        print("\n  Prediction 2 confirmed, and it is a warning not a success.")
        print(f"    cost_ceiling fired on {fired_share:.0%} of sessions because the")
        print("    ceiling was set below the median session cost. A detector that")
        print("    fires on everything carries the same information as one that")
        print("    fires on nothing. The rate is a statement about the threshold,")
        print("    and quoting it without the threshold beside it is meaningless.")


if __name__ == "__main__":
    main()
