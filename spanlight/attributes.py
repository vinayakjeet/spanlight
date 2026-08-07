"""Every attribute name Spanlight emits, in one place.

The OTel GenAI semantic conventions are still moving. Keeping the names here
means a convention rename is a one-file change, and `SEMCONV_VERSION` goes on
the resource so a stored trace says which revision produced it. A trace whose
attribute names came from a different revision than the dashboard querying it is
worse than no trace, because it looks queryable and returns nothing.

`CONTRACT` is the published set, and `tests/spanlight/test_attributes.py` holds
it to the table in SPEC.md in both directions. A hand-maintained table always
drifts from the code eventually, and this turns the drift into a test failure
rather than a surprise weeks later when a dashboard quietly returns nothing.
"""

from __future__ import annotations

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

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

COLD_START = "spanlight.cold_start"

ERROR_TYPE = "error.type"
DETECTION = "spanlight.detection"

SEMCONV_VERSION = "1.29.0"
SEMCONV_VERSION_ATTRIBUTE = "spanlight.semconv_version"

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

DETECTIONS_TOTAL = "spanlight_detections_total"

EVENT_CONTRACT = frozenset(
    {
        DETECTION_TYPE,
        DETECTION_TOOL_NAME,
        DETECTION_TOOL_CALLS,
        DETECTION_COST_USD_EQUIVALENT,
        DETECTION_COST_CEILING_USD,
    }
)

CONTRACT = frozenset(
    {
        GEN_AI_SYSTEM,
        GEN_AI_OPERATION_NAME,
        GEN_AI_REQUEST_MODEL,
        GEN_AI_RESPONSE_MODEL,
        GEN_AI_INPUT_TOKENS,
        GEN_AI_OUTPUT_TOKENS,
        COST_USD,
        COST_USD_EQUIVALENT,
        SESSION_ID,
        TOOL_NAME,
        TOOL_ARGS_FINGERPRINT,
        RETRIEVAL_INDEX,
        RETRIEVAL_K,
        COLD_START,
        SEMCONV_VERSION_ATTRIBUTE,
        ERROR_TYPE,
        DETECTION,
    }
)
