"""Drop-in OpenTelemetry instrumentation for LLM agents.

Three lines to adopt:

    import spanlight
    spanlight.init(service="my-service")

    with spanlight.model_span(provider="gemini", model="gemini-flash-latest"):
        ...
"""

from __future__ import annotations

from spanlight._propagation import headers
from spanlight._session import current_session_id
from spanlight._setup import init
from spanlight._spans import (
    attempt_span,
    fingerprint,
    get_tracer,
    model,
    model_span,
    retrieval_span,
    session,
    tool,
    tool_span,
)
from spanlight._usage import cost_usd_equivalent, record_usage

__all__ = [
    "attempt_span",
    "cost_usd_equivalent",
    "current_session_id",
    "fingerprint",
    "get_tracer",
    "headers",
    "init",
    "model",
    "model_span",
    "record_usage",
    "retrieval_span",
    "session",
    "tool",
    "tool_span",
]
