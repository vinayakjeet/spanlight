"""How often the detectors fire on sessions that were fine.

    PYTHONPATH=. uv run python bench/false_positives.py

A detector nobody trusts is worse than no detector, because it trains its reader
to dismiss it. ShipGate shipped a gate threshold of 2 points against a judge
whose measured noise floor was 20, set by intuition, and every run it flagged was
noise. `LOOP_THRESHOLD = 3` here was picked the same way, so it gets measured
before the README quotes it.

**What this corpus is.** Synthetic healthy sessions, built from patterns a real
agent produces: pagination, retries after a transient failure, multi-step plans,
recovery by trying a different tool. It measures the detectors against a model of
healthy behaviour rather than against reality, so a rate of zero here means "does
not fire on anything I thought of", not "does not fire". That is worth having
anyway: a default that misfires on a plain retry is wrong regardless of whose
corpus finds it. The real number comes from the M7 field corpus and replaces
this one.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
import spanlight._spans as spans_module
from spanlight._detector_framework import SESSION, registry
from spanlight._detectors import (
    loop_detector,
    silent_tool_failure_detector,
    watch_for_silent_failure,
)
from spanlight.attributes import DETECTION

SESSIONS_PER_PATTERN = 100
SEED = 20260808


class TransientFailure(Exception):
    pass


def _model() -> None:
    with spanlight.model_span(provider="groq"):
        spanlight.record_usage(
            tokens_in=random.randint(200, 900),
            tokens_out=random.randint(40, 300),
            cost_usd=0.0,
            provider="groq",
        )


def single_step() -> None:
    """The common case. One lookup, one answer."""
    with spanlight.tool_span("lookup_scheme", args={"id": random.randint(1, 10_000)}):
        pass
    _model()


def paginated_search() -> None:
    """Same tool repeatedly, walking pages. Different arguments each time, so the
    fingerprints differ and this should never look like a loop."""
    for page in range(random.randint(3, 6)):
        with spanlight.tool_span("search", args={"q": "irrigation subsidy", "page": page}):
            pass
    _model()


def transient_retry() -> None:
    """A tool that fails twice and succeeds on the third go, with identical
    arguments every time. This is what a network blip looks like, and it is the
    pattern most likely to be mistaken for an agent stuck in a loop."""
    args = {"q": "pm-kisan eligibility"}
    for attempt in range(3):
        if attempt < 2:
            try:
                with spanlight.tool_span("search", args=args):
                    raise TransientFailure("connection reset")
            except TransientFailure:
                pass
        else:
            with spanlight.tool_span("search", args=args):
                pass
    _model()


def multi_tool_plan() -> None:
    """A plan touching several different tools once each."""
    for tool in ("search", "lookup_scheme", "check_eligibility", "summarise"):
        with spanlight.tool_span(tool, args={"q": random.randint(1, 10_000)}):
            pass
        _model()


def repeated_lookup_distinct_entities() -> None:
    """The same tool many times over, which is correct behaviour when each call
    asks about something different."""
    for _ in range(random.randint(4, 9)):
        with spanlight.tool_span("lookup_scheme", args={"id": random.randint(1, 10_000)}):
            pass
    _model()


def recovery_via_another_tool() -> None:
    """A tool fails, the agent tries a different one, succeeds, and the run ends
    cleanly. The agent handled the failure; it did not hide it."""
    try:
        with spanlight.tool_span("search", args={"q": "pm-kisan"}):
            raise TransientFailure("index unavailable")
    except TransientFailure:
        pass
    with spanlight.tool_span("lookup_scheme", args={"id": 42}):
        pass
    _model()


def long_research() -> None:
    """A long run asking many distinct questions."""
    for i in range(random.randint(10, 16)):
        with spanlight.tool_span("search", args={"q": f"district-{i} rainfall"}):
            pass
        if i % 3 == 0:
            _model()
    _model()


PATTERNS: dict[str, Callable[[], None]] = {
    "single_step": single_step,
    "paginated_search": paginated_search,
    "transient_retry": transient_retry,
    "multi_tool_plan": multi_tool_plan,
    "repeated_lookup": repeated_lookup_distinct_entities,
    "recovery_via_another_tool": recovery_via_another_tool,
    "long_research": long_research,
}


def wilson(fired: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a proportion.

    Wilson rather than the textbook normal approximation, because these rates sit
    near zero where the normal one produces negative lower bounds and a
    misleadingly tight interval on a small count.
    """
    if total == 0:
        return (0.0, 0.0)
    p = fired / total
    denom = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def measure(sessions_per_pattern: int = SESSIONS_PER_PATTERN) -> dict[str, Counter]:
    random.seed(SEED)

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Restored in the `finally`. `tests/spanlight/test_false_positive_rate.py`
    # imports this, so leaving the swap in place hands every later test in the
    # process this provider, and the ones that assert on tracing being *off*
    # start failing somewhere unrelated.
    original_tracer = spans_module.get_tracer
    spans_module.get_tracer = lambda: provider.get_tracer("false-positives")

    registry.clear_detectors()
    registry.register(loop_detector)
    registry.register(watch_for_silent_failure)
    registry.register(silent_tool_failure_detector, phase=SESSION)

    results: dict[str, Counter] = {}
    try:
        for name, pattern in PATTERNS.items():
            fired: Counter = Counter()
            for _ in range(sessions_per_pattern):
                exporter.clear()
                with spanlight.session():
                    pattern()
                detections = {
                    span.attributes[DETECTION]
                    for span in exporter.get_finished_spans()
                    if DETECTION in (span.attributes or {})
                }
                for detection in detections:
                    fired[detection] += 1
                fired["sessions"] += 1
            results[name] = fired
    finally:
        spans_module.get_tracer = original_tracer
        registry.clear_detectors()
        registry.reset()
    return results


def main() -> None:
    results = measure()
    kinds = ("loop", "silent_tool_failure")

    width = max(len(name) for name in results)
    print(f"{sessions_note(results)}\n")
    print(f"{'pattern':{width}}  {'loop':>18}  {'silent_tool_failure':>22}")
    for name, fired in results.items():
        cells = []
        for kind in kinds:
            n, total = fired[kind], fired["sessions"]
            low, high = wilson(n, total)
            cells.append(f"{n:>3}/{total:<3} {low:5.1%}-{high:5.1%}")
        print(f"{name:{width}}  {cells[0]:>18}  {cells[1]:>22}")

    print()
    for kind in kinds:
        n = sum(f[kind] for f in results.values())
        total = sum(f["sessions"] for f in results.values())
        low, high = wilson(n, total)
        verdict = "clean" if n == 0 else "MISFIRES"
        print(f"{kind:22} {n:>4}/{total:<4}  95% CI {low:5.1%} to {high:5.1%}  {verdict}")


def sessions_note(results: dict[str, Counter]) -> str:
    total = sum(f["sessions"] for f in results.values())
    return (
        f"{total} synthetic healthy sessions across {len(results)} patterns, "
        f"seed {SEED}.\nA rate of zero means the detector does not fire on any "
        "pattern here, not that it never fires."
    )


if __name__ == "__main__":
    main()
