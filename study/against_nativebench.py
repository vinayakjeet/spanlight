"""The field corpus against AI-NativeBench's three published findings.

    PYTHONPATH=. uv run python study/against_nativebench.py

AI-NativeBench (arXiv 2601.09393) treats agent spans as first-class citizens and
reports three findings: lightweight models sometimes beat flagships on protocol
adherence (the parameter paradox), model inference time dwarfs protocol overhead,
and self-healing retry mechanisms act as cost multipliers on workflows that were
never going to succeed. This corpus was not collected to test those claims, which
is exactly why testing them is worth doing: confirming or refuting a published
finding on data gathered for other purposes is harder evidence than a study
built to please its own hypothesis.

Every verdict below names what the corpus cannot support before it reports what
it can. A comparison that quietly skips the untestable findings reads as three
for three.
"""

from __future__ import annotations

import statistics

from study.analyse import COST_EQUIVALENT, load, sessions, table

# The attribute ShipGate measures inside its concurrency semaphore, so unlike
# the session span it does not count queue time as inference. See analyse.py
# and study/traces.md trace 2, where reading the wrong denominator published a
# latency table that was 51.9% queueing.
CONTAMINANT_FREE_LATENCY = "shipgate.latency_ms"

DOMINANCE_THRESHOLD = 0.8


def inference_dominance(runs: dict) -> dict:
    """Share of a session's real work spent inside the model call.

    Denominator is `shipgate.latency_ms` where present and the session span
    duration otherwise. With the session span, waiting in the batch counts
    against dominance; with the runner's own measurement it does not. Both are
    reported because both are true and neither is the whole truth.
    """
    shares = {"measured": [], "session_span": []}
    for record in runs.values():
        if not record["model_calls"] or record["item"] is None:
            continue
        model_ms = sum(c["duration_ms"] for c in record["model_calls"])
        clean = record["item"]["attributes"].get(CONTAMINANT_FREE_LATENCY)
        if clean:
            shares["measured"].append(model_ms / clean)
        shares["session_span"].append(model_ms / record["item"]["duration_ms"])

    return {
        group: {
            "n": len(values),
            "median": statistics.median(values),
            "p90": sorted(values)[min(len(values) - 1, int(0.9 * len(values)))],
        }
        for group, values in shares.items()
        if values
    }


def failure_costs(runs: dict) -> tuple[float | None, float | None]:
    """Median equivalent cost of failed versus clean sessions.

    Either side can be empty, and None means no observation rather than zero:
    a corpus with no failures cannot report a failure-cost ratio, and writing
    0.0 there would be read as failures being free.
    """
    failed: list[float] = []
    clean: list[float] = []
    for record in runs.values():
        cost = sum(c["attributes"].get(COST_EQUIVALENT, 0.0) for c in record["model_calls"])
        if record["item"]["status"] == "ERROR":
            failed.append(cost)
        else:
            clean.append(cost)

    failed_median = statistics.median(failed) if failed else None
    clean_median = statistics.median(clean) if clean else None
    return failed_median, clean_median


def models_served(runs: dict) -> set[str]:
    """Distinct `gen_ai.response.model` values across every model call."""
    return {
        call["attributes"]["gen_ai.response.model"]
        for record in runs.values()
        for call in record["model_calls"]
        if "gen_ai.response.model" in call["attributes"]
    }


def attempt_spans(spans: list[dict]) -> int:
    """Spans emitted inside a retry loop, added to the library after this
    corpus was collected."""
    return sum(1 for s in spans if s["name"].startswith("attempt "))


def main() -> None:
    spans = load()
    runs = sessions(spans)
    total = len(runs)
    print(f"corpus: {len(spans)} spans, {total} sessions")

    print("\n=== Finding 2: inference dominance ===")
    dominance = inference_dominance(runs)
    rows = [
        (
            group,
            stats["n"],
            f"{stats['median']:.1%}",
            f"{stats['p90']:.1%}",
        )
        for group, stats in dominance.items()
    ]
    table(
        "share of the session that is model time",
        rows,
        ("denominator", "n", "median", "p90"),
    )
    measured = dominance.get("measured")
    if measured and measured["median"] >= DOMINANCE_THRESHOLD:
        print(f"\n  Consistent with AI-NativeBench at median {measured['median']:.1%}:")
        print("  protocol overhead is real but small next to the provider call.")
        print(f"  Threshold for 'dominant' stated up front: {DOMINANCE_THRESHOLD:.0%}.")
    elif measured:
        print(
            f"\n  Not reproduced: median share {measured['median']:.1%} sits below"
            f" the stated {DOMINANCE_THRESHOLD:.0%}."
        )
    print("  One system, one provider, one workload shape; this corroborates on")
    print("  ShipGate-shaped traffic and generalises no further than it.")

    print("\n=== Finding 3: failure as a cost multiplier ===")
    failed_median, clean_median = failure_costs(runs)
    if failed_median is None:
        print("  No failed sessions in this corpus, so the multiplier is")
        print("  unmeasurable here, and unmeasured beats invented.")
    else:
        assert clean_median is not None
        print(f"  median failed ${failed_median:.8f} vs clean ${clean_median:.8f}")

    attempts = attempt_spans(spans)
    print(f"\n  retry evidence in the corpus: {attempts} attempt spans")
    print("  Structurally zero: collection predates attempt spans, and the judge")
    print("  wraps the whole retry loop in one model span, so retries were only")
    print("  ever visible as duration. Whether retries multiply cost on this")
    print("  workload waits on the second corpus (BACKLOG M9.4), which will have")
    print("  the spans to answer it.")

    print("\n=== Finding 1: the parameter paradox ===")
    served = models_served(runs)
    print(f"  distinct models served: {len(served)} ({sorted(served)})")
    if len(served) < 2:
        print("  One model end to end, so lightweight-versus-flagship adherence")
        print("  has nothing to compare. Not testable on this corpus, by design:")
        print("  the free-tier constraint fixed the model before the question")
        print("  existed.")


if __name__ == "__main__":
    main()
