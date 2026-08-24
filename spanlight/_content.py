"""Opt-in prompt and completion capture, following the convention's own shape.

Off by default, and off in every portfolio project. The convention defines
`gen_ai.input.messages` and friends as Opt-In attributes carrying formal JSON
schemas precisely so that prompt text has a sanctioned home for callers who
have decided they want it, instead of being stuffed into ad-hoc attributes
where size limits and PII exposure are discovered later.

The gate is the ecosystem's own variable,
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, so an operator who has
already made this decision once for their other instrumentation makes it once
for Spanlight too. `SPAN_ONLY` and `SPAN_AND_EVENT` both capture onto spans;
Spanlight emits no log events, so `EVENT_ONLY` leaves capture off rather than
silently meaning something else.
"""

from __future__ import annotations

import json
import os
from typing import Any

from opentelemetry import trace

from spanlight.attributes import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
)

CAPTURE_CONTENT_VAR = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
_SPAN_MODES = frozenset({"SPAN_ONLY", "SPAN_AND_EVENT"})


def capture_enabled() -> bool:
    """Whether message bodies may be written to the current span."""
    return os.environ.get(CAPTURE_CONTENT_VAR, "").strip().upper() in _SPAN_MODES


def record_content(
    *,
    system_instructions: list[dict[str, Any]] | None = None,
    input_messages: list[dict[str, Any]] | None = None,
    output_messages: list[dict[str, Any]] | None = None,
) -> bool:
    """Attach message bodies to the current model span as JSON strings.

    Returns whether anything was written. With capture disabled the call is a
    genuine no-op, which is what lets a call site stay in place unconditionally
    the way every other Spanlight call site does: the decision belongs to the
    operator's environment, not to the code path.

    The caller owns the message structure; each list is serialised as given.
    The convention's schemas put text under `parts[].content`, and a caller
    whose dictionaries do not follow that shape produces an attribute no
    normalizer or dashboard can read, but nothing here second-guesses them:
    truncating or rewriting user payloads would be its own surprise.
    """
    if not capture_enabled():
        return False

    span = trace.get_current_span()
    if not span.is_recording():
        return False

    if system_instructions is not None:
        span.set_attribute(GEN_AI_SYSTEM_INSTRUCTIONS, json.dumps(system_instructions))
    if input_messages is not None:
        span.set_attribute(GEN_AI_INPUT_MESSAGES, json.dumps(input_messages))
    if output_messages is not None:
        span.set_attribute(GEN_AI_OUTPUT_MESSAGES, json.dumps(output_messages))
    return True
