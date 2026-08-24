"""Every attribute name Spanlight emits, in one place.

The OTel GenAI semantic conventions are still moving. Keeping the names here
means a convention rename is a one-file change, and `SEMCONV_VERSION` goes on
the resource so a stored trace says which revision produced it. A trace whose
attribute names came from a different revision than the dashboard querying it is
worse than no trace, because it looks queryable and returns nothing.

Names are resolved through `resolve` at emission time, which consults
`OTEL_SEMCONV_STABILITY_OPT_IN` and the rename table above, so a host can move
to the latest development names without a code change. Emission sites use
`resolve(GEN_AI_SYSTEM)`, not the bare constant, for exactly that reason.

`CONTRACT` is the published set, and `tests/spanlight/test_attributes.py` holds
it to the table in docs/CONTRACT.md in both directions. A hand-maintained table
always drifts from the code eventually, and this turns the drift into a test
failure rather than a surprise weeks later when a dashboard quietly returns
nothing.
"""

from __future__ import annotations

import os

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# Content capture. Opt-in, and off everywhere in this portfolio: the names and
# the JSON shape follow the convention's own opt-in attributes, which exist so
# that prompt text has a sanctioned place to live for callers who have already
# decided they want it, instead of being stuffed into ad-hoc attributes.
GEN_AI_SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"

# What was actually spent, which on a free tier is 0.0 and true.
COST_USD = "spanlight.cost_usd"
# What the same tokens would have cost at published list prices. A separate name
# because a counterfactual presented as spend is a lie waiting to be quoted.
COST_USD_EQUIVALENT = "spanlight.cost_usd_equivalent"

SESSION_ID = "spanlight.session.id"

TOOL_NAME = "spanlight.tool.name"
TOOL_ARGS_FINGERPRINT = "spanlight.tool.args_fingerprint"

RETRIEVAL_INDEX = "spanlight.retrieval.index"
RETRIEVAL_K = "spanlight.retrieval.k"

# One per attempt inside a retry loop, 1-based. The model span still wraps the
# whole loop, because its duration is what the caller actually waited, backoff
# and throttle sleeps included. These are its children, and without them a call
# that succeeded on the fourth try is indistinguishable from one that succeeded
# on the first.
ATTEMPT_NUMBER = "spanlight.attempt.number"

COLD_START = "spanlight.cold_start"

ERROR_TYPE = "error.type"
DETECTION = "spanlight.detection"

SEMCONV_VERSION = "1.29.0"
SEMCONV_VERSION_ATTRIBUTE = "spanlight.semconv_version"

# The convention's own transition mechanism. Nothing GenAI is Stable, and the
# spec tells instrumentations to pin what they emit and to accept
# `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` as the switch to
# the newest development names. The pinned revision is the default; setting the
# variable moves emission onto the latest names this library knows about.
STABILITY_OPT_IN = "OTEL_SEMCONV_STABILITY_OPT_IN"
LATEST_EXPERIMENTAL = "gen_ai_latest_experimental"

# Names that differ between the pinned revision and the latest development
# document, pinned name first. One entry per rename, so the day another lands
# it is one line here rather than a grep across every call site.
#
# The one entry that exists today: `gen_ai.system` became
# `gen_ai.provider.name` in the development conventions, which now require it on
# inference spans. Every other name Spanlight emits is identical across both
# generations, and an empty table would have been honest too; the mapping exists
# so that agreeing today does not have to be maintained by memory.
EXPERIMENTAL_RENAMES: dict[str, str] = {
    GEN_AI_SYSTEM: GEN_AI_PROVIDER_NAME,
}


def experimental_opt_in() -> bool:
    """Whether emission should follow the latest development names.

    The variable holds a comma-separated list of category values, so a host
    opting into several migrations at once still parses. Anything unrecognised
    is ignored rather than rejected: the variable belongs to the ecosystem, and
    failing on a value meant for some other instrumentation would make Spanlight
    harder to adopt beside that instrumentation.
    """
    values = os.environ.get(STABILITY_OPT_IN, "")
    return LATEST_EXPERIMENTAL in {v.strip() for v in values.split(",") if v.strip()}


def resolve(attribute: str) -> str:
    """The name to emit for `attribute` under the active generation.

    Every call site that writes a GenAI attribute goes through here rather than
    using its constant directly. Today exactly one name differs, so the function
    reads as ceremony; it is the mechanism that keeps it at one file when it
    stops being one name.
    """
    if experimental_opt_in():
        return EXPERIMENTAL_RENAMES.get(attribute, attribute)
    return attribute


def semconv_generation() -> str:
    """What `spanlight.semconv_version` records on the resource."""
    if experimental_opt_in():
        return f"{SEMCONV_VERSION}+{LATEST_EXPERIMENTAL}"
    return SEMCONV_VERSION


TRACER_NAME = "spanlight"

# The span event a detection adds alongside the attribute. The attribute answers
# "did this run trip anything", which is what a dashboard groups by; the event
# answers "why", which is what a human reads once the dashboard has pointed at a
# trace. Kept apart from CONTRACT because these are event attributes, not span
# attributes, and a query written against the wrong one silently returns nothing.
DETECTION_EVENT = "spanlight.detection"
DETECTION_TYPE = "spanlight.detection.type"
DETECTION_TOOL_NAME = "spanlight.detection.tool.name"
DETECTION_TOOL_CALLS = "spanlight.detection.tool.calls"
DETECTION_COST_USD_EQUIVALENT = "spanlight.detection.cost.usd_equivalent"
DETECTION_COST_CEILING_USD = "spanlight.detection.cost.ceiling_usd"
DETECTION_RETRY_FAILED_ATTEMPTS = "spanlight.detection.retry.failed_attempts"
DETECTION_RETRY_THRESHOLD = "spanlight.detection.retry.threshold"

DETECTIONS_TOTAL = "spanlight_detections_total"

# An export that fails is invisible from inside the process: the batch processor
# swallows it, the agent carries on, and the only symptom is traces that are not
# there. Nothing is a reliable alert on absence, so the failure has to be counted
# where it happens.
EXPORT_FAILURES_TOTAL = "spanlight_export_failures_total"

# Declared here, emitted in M6. Named now so the label contract below covers the
# whole SPEC table rather than only the parts already built, which is when a
# label is easiest to get wrong.
SESSION_COST_USD = "spanlight_session_cost_usd"
TOKEN_USAGE = "gen_ai_client_token_usage"

# Every label every metric is allowed to carry.
#
# A span attribute with too many values costs one wide trace. A metric label with
# too many values costs one time series per value, forever, and the bill and the
# Prometheus instance both fail well before anyone notices the cause. Session id
# is the obvious foot-gun: it is on every span, it reads like a useful grouping,
# and adding it here would create a series per run and exhaust the free tier's
# active-series limit within days.
METRIC_LABELS: dict[str, frozenset[str]] = {
    DETECTIONS_TOTAL: frozenset({"type", "service"}),
    EXPORT_FAILURES_TOTAL: frozenset({"reason", "service"}),
    SESSION_COST_USD: frozenset({"service"}),
    TOKEN_USAGE: frozenset({"gen_ai.system", "service", "type"}),
}

EVENT_CONTRACT = frozenset(
    {
        DETECTION_TYPE,
        DETECTION_TOOL_NAME,
        DETECTION_TOOL_CALLS,
        DETECTION_COST_USD_EQUIVALENT,
        DETECTION_COST_CEILING_USD,
        DETECTION_RETRY_FAILED_ATTEMPTS,
        DETECTION_RETRY_THRESHOLD,
    }
)

CONTRACT = frozenset(
    {
        GEN_AI_SYSTEM,
        GEN_AI_PROVIDER_NAME,
        GEN_AI_OPERATION_NAME,
        GEN_AI_REQUEST_MODEL,
        GEN_AI_RESPONSE_MODEL,
        GEN_AI_INPUT_TOKENS,
        GEN_AI_OUTPUT_TOKENS,
        GEN_AI_SYSTEM_INSTRUCTIONS,
        GEN_AI_INPUT_MESSAGES,
        GEN_AI_OUTPUT_MESSAGES,
        COST_USD,
        COST_USD_EQUIVALENT,
        SESSION_ID,
        TOOL_NAME,
        TOOL_ARGS_FINGERPRINT,
        RETRIEVAL_INDEX,
        RETRIEVAL_K,
        ATTEMPT_NUMBER,
        COLD_START,
        SEMCONV_VERSION_ATTRIBUTE,
        ERROR_TYPE,
        DETECTION,
    }
)
