from __future__ import annotations

from collections.abc import Callable

from opentelemetry.trace import Span, StatusCode

from spanlight._detector_framework import Detection
from spanlight.attributes import (
    COST_USD_EQUIVALENT,
    DETECTION_COST_CEILING_USD,
    DETECTION_COST_USD_EQUIVALENT,
    DETECTION_TOOL_CALLS,
    DETECTION_TOOL_NAME,
    GEN_AI_SYSTEM,
    TOOL_ARGS_FINGERPRINT,
    TOOL_NAME,
)

LOOP_THRESHOLD = 3

LOOP = "loop"
COST_CEILING = "cost_ceiling"
SILENT_TOOL_FAILURE = "silent_tool_failure"


def loop_detector(state: dict, span: Span) -> str | None:
    """Fire when one tool is called with identical arguments too many times.

    Identity is the salted fingerprint, never the arguments themselves, so a
    detection can be reported without the trace carrying what was searched for.
    Arguments differing by one character produce a different fingerprint and are
    counted separately, which is the negative case that keeps the detector off an
    agent that is making progress.
    """
    attributes = span.attributes or {}
    name = attributes.get(TOOL_NAME)
    args_fingerprint = attributes.get(TOOL_ARGS_FINGERPRINT)
    if not name or not args_fingerprint:
        return None

    # Only calls that succeeded. Counting failures made a plain retry
    # indistinguishable from a loop: a tool that fails twice on a network blip
    # and succeeds on the third attempt sends the same arguments three times, and
    # `bench/false_positives.py` measured that firing on 100% of retry sessions.
    #
    # The distinction is real rather than a fudge. An agent in a loop is getting
    # answers and asking again anyway; an agent retrying is not getting answers
    # yet. Only the first is stuck.
    if span.status.status_code is StatusCode.ERROR:
        return None

    counts = state.setdefault("tool_calls", {})
    key = (name, args_fingerprint)
    counts[key] = counts.get(key, 0) + 1
    if counts[key] < LOOP_THRESHOLD:
        return None

    # The tool name and the count, never the fingerprint. The fingerprint is
    # salted per process, so publishing it in an event would be noise to a
    # reader and would not survive into the study corpus anyway.
    return Detection(
        LOOP,
        {DETECTION_TOOL_NAME: name, DETECTION_TOOL_CALLS: counts[key]},
    )


def cost_ceiling_detector(ceiling_usd: float) -> Callable[[dict, Span], str | None]:
    """Fire when a session's model calls pass a spend ceiling.

    Reads `spanlight.cost_usd_equivalent`, the counterfactual, rather than
    `spanlight.cost_usd`. Every price in the chassis `quotas.yaml` is zero
    because these are free tiers, so a ceiling watching real spend would sit at
    zero forever and could never fire. A detector that cannot fire is the thing
    ShipGate shipped twice, and it passes its own unit tests both times.

    The ceiling is a closure argument rather than a default parameter so the
    value is fixed where the detector is registered and appears in the call
    stack, instead of being an invisible constant the registry cannot pass.

    Observing only. SPEC S6 requires the run to continue after this fires: the
    agent is not cancelled and no exception is raised, because enforcement
    belongs to Dwarpal and Chakravyuh, not here.
    """

    def detect(state: dict, span: Span) -> Detection | None:
        equivalent = (span.attributes or {}).get(COST_USD_EQUIVALENT)
        if not equivalent:
            return None

        total = state.get("cost_usd_equivalent", 0.0) + equivalent
        state["cost_usd_equivalent"] = total
        if total <= ceiling_usd:
            return None

        # Both numbers, because a breach is only meaningful next to the line it
        # crossed, and the ceiling is a deployment choice a reader will not know.
        return Detection(
            COST_CEILING,
            {
                DETECTION_COST_USD_EQUIVALENT: total,
                DETECTION_COST_CEILING_USD: ceiling_usd,
            },
        )

    return detect


def watch_for_silent_failure(state: dict, span: Span) -> None:
    """Record the two facts the session-end verdict needs. Never fires itself.

    Runs in the span phase purely to observe, because the question it feeds
    cannot be answered until the session closes. Returning `None` unconditionally
    keeps it from consuming the one detection slot a span gets.
    """
    attributes = span.attributes or {}
    failed = span.status.status_code is StatusCode.ERROR

    if attributes.get(TOOL_NAME):
        if failed:
            state["failed_tool"] = attributes[TOOL_NAME]
        elif state.get("failed_tool"):
            # A tool succeeded after the failure, so the run recovered: either it
            # retried and got through, or it found another way. Either way the
            # agent had real data to answer from, which is the opposite of the
            # failure this detector is for.
            state["recovered"] = True
        return None

    if attributes.get(GEN_AI_SYSTEM) and state.get("failed_tool"):
        state["model_ran_after_tool_failed"] = True
    return None


def silent_tool_failure_detector(state: dict, span: Span) -> Detection | None:
    """Fire when a run was told a tool failed and reported success anyway.

    The rule, from SPEC S4: a tool span ended ERROR, a model span ran after it in
    the same session, and the session itself did not end ERROR. That third clause
    is why this cannot run per-span. While the run is in progress its final
    status is unknown, and an agent that recovers from a failed tool and then
    correctly reports failure is behaving well.

    Non-ERROR rather than explicitly OK, because OpenTelemetry leaves a span's
    status UNSET unless something sets it, so demanding OK would mean this only
    ever fired for callers who set a status by hand.
    """
    if not state.get("model_ran_after_tool_failed"):
        return None
    if span.status.status_code is StatusCode.ERROR:
        return None
    # No tool succeeded after the failure, so the run answered from the model
    # alone. Measurement forced this clause: without it the rule fired on every
    # retry and every recovery, 28.6% of healthy sessions, because "a tool
    # failed and the run finished OK" describes competent error handling just as
    # well as it describes ignoring the error.
    if state.get("recovered"):
        return None

    # Names the tool, because the detection lands on the session span and the
    # failed step is otherwise somewhere in a waterfall the reader has to search.
    return Detection(
        SILENT_TOOL_FAILURE, {DETECTION_TOOL_NAME: state["failed_tool"]}
    )
