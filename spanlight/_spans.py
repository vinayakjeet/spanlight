from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import secrets
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.context import attach, detach
from opentelemetry.trace import Span, Status, StatusCode

from spanlight._detector_framework import SESSION, SPAN, registry
from spanlight._propagation import remote_context, remote_session_id
from spanlight._session import bind, current_session_id
from spanlight.attributes import (
    ERROR_TYPE,
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM,
    RETRIEVAL_INDEX,
    RETRIEVAL_K,
    SESSION_ID,
    TOOL_ARGS_FINGERPRINT,
    TOOL_NAME,
    TRACER_NAME,
)

FINGERPRINT_LENGTH = 16
SESSION_SPAN_NAME = "session"


def get_tracer() -> trace.Tracer:
    """Safe to call unconditionally.

    With no provider configured OpenTelemetry returns a no-op tracer, so
    instrumented code costs effectively nothing rather than needing an
    `if tracing_enabled` around every call site.
    """
    return trace.get_tracer(TRACER_NAME)


@functools.cache
def _salt() -> str:
    """Salt for tool-argument fingerprints.

    Random per process by default, so the published M7 trace corpus cannot be
    brute forced back into the arguments it hashed. Short tool arguments (a
    search query, an id) have little enough entropy that an unsalted digest is
    recoverable by anyone willing to guess.

    Set `SPANLIGHT_FINGERPRINT_SALT` to compare fingerprints across processes,
    which the study corpus wants and which M3.2 loop detection does not, since
    it only ever compares within a single live session.
    """
    return os.environ.get("SPANLIGHT_FINGERPRINT_SALT") or secrets.token_hex(16)


def fingerprint(args: object) -> str:
    """Short stable hash of tool arguments.

    Loop detection has to know two calls were identical without the trace
    carrying what they contained. Keys are sorted so that argument ordering
    cannot make two identical calls look different, and `default=repr` keeps a
    non-serializable argument from raising inside instrumentation.
    """
    canonical = json.dumps(args, sort_keys=True, default=repr)
    digest = hashlib.sha256(f"{_salt()}{canonical}".encode()).hexdigest()
    return digest[:FINGERPRINT_LENGTH]


@contextmanager
def _span(name: str, attributes: dict[str, Any], phase: str = SPAN) -> Iterator[Span]:
    """Open a span, stamp the session, and record failures honestly.

    Every public span helper goes through here, so the session id and the error
    contract cannot be present on one kind of span and missing on another.

    The session id is stamped here rather than by a `SpanProcessor.on_start`
    hook, which is the more idiomatic OTel answer. A processor only exists once
    the SDK is configured, so the attribute would vanish in exactly the tests
    written to prove it is there.
    """
    session_id = current_session_id()
    if session_id is not None:
        attributes[SESSION_ID] = session_id

    # Both defaults are on, and both undo the error contract below. With
    # `record_exception` the SDK attaches an event carrying `exception.message`
    # and a full `exception.stacktrace`, so the message this code is careful
    # never to record arrives anyway, with a stack trace around it.
    # `set_status_on_exception` then overwrites the status description with that
    # same message. A redaction canary found both; nothing else would have,
    # because the attribute we do set is correct and the leak is beside it.
    with get_tracer().start_as_current_span(
        name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except Exception as exc:
            # The class name, never the message. Messages carry user data and
            # give the attribute unbounded cardinality. Without this a failed
            # call produces a span reporting OK, which is the failure class this
            # whole project exists to make visible.
            span.set_attribute(ERROR_TYPE, type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            # Last moment the span is still writable, first moment its status is
            # final. A SpanProcessor cannot do this: `on_end` hands out a
            # ReadableSpan, which has no `set_attribute`.
            registry.run(span, phase)


@contextmanager
def session(
    session_id: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> Iterator[str]:
    """Group everything inside into one session, as one span.

    A session is the unit the detectors reason over and the unit the M7 study
    counts, so it has to mean one logical agent run rather than one process or
    one HTTP request.

    It is a span and not merely a context variable, which it was until this was
    caught. Without an enclosing span every step in a run is a parentless root,
    so a three-step session arrives in Tempo as three unrelated traces that
    happen to share an attribute. There is no waterfall to read, S8's promise
    that an exported session keeps its children is vacuous because it has none,
    and a session-scoped detector has nothing still open to mark.

    Yields the id rather than the span, because callers want something they can
    hand back to a user to find the run with. The span is reachable through
    `opentelemetry.trace.get_current_span()` for the rare caller who needs it.

    Pass `headers` from an inbound request and the run joins the caller's trace
    and adopts the session id from its baggage:

        with spanlight.session(headers=request.headers) as session_id:

    An agent that calls a second service is one logical run, so it has to be one
    session. Generating a fresh id per hop would split it, and the study would
    then count a two-service failure as two unrelated short sessions. An explicit
    `session_id` still wins over the inbound one, so a caller that knows better
    can say so.
    """
    remote = remote_context(headers) if headers is not None else None
    resolved = session_id or (remote_session_id(remote) if remote else None)
    resolved = resolved or uuid.uuid4().hex

    token = attach(remote) if remote is not None else None
    try:
        with bind(resolved):
            try:
                with _span(SESSION_SPAN_NAME, {}, phase=SESSION):
                    yield resolved
            finally:
                # Detector scratch space for a finished run is garbage. Releasing
                # it here is what keeps the registry holding only sessions in
                # flight; the LRU and TTL are the backstop for runs that never
                # get here.
                registry.release(resolved)
    finally:
        if token is not None:
            detach(token)


@contextmanager
def model_span(
    provider: str, model: str | None = None, operation: str = "chat"
) -> Iterator[Span]:
    """Wrap a model call in a span carrying the GenAI convention attributes.

    This is the primary form, not a convenience on top of the decorator. The one
    real call site in this repo picks its provider from a settings value per
    request, so there is nothing to name at import time. `model` is optional for
    the same reason: the chassis client passes no model and lets the provider
    choose, and QUOTAS.md records that `gemini-flash-latest` is a moving alias,
    so what the provider actually served is worth more than what was asked for.
    Callers set `gen_ai.response.model` on the yielded span once they know.
    """
    attributes: dict[str, Any] = {
        GEN_AI_SYSTEM: provider,
        GEN_AI_OPERATION_NAME: operation,
    }
    if model is not None:
        attributes[GEN_AI_REQUEST_MODEL] = model

    with _span(f"{operation} {model}" if model else operation, attributes) as span:
        yield span


@contextmanager
def tool_span(name: str, args: object = None) -> Iterator[Span]:
    """Wrap a tool call. Arguments are fingerprinted, never recorded."""
    attributes: dict[str, Any] = {TOOL_NAME: name}
    if args is not None:
        attributes[TOOL_ARGS_FINGERPRINT] = fingerprint(args)

    with _span(f"tool {name}", attributes) as span:
        yield span


@contextmanager
def retrieval_span(index: str, k: int | None = None) -> Iterator[Span]:
    """Wrap a retrieval call."""
    attributes: dict[str, Any] = {RETRIEVAL_INDEX: index}
    if k is not None:
        attributes[RETRIEVAL_K] = k

    with _span(f"retrieve {index}", attributes) as span:
        yield span


def _wrap(
    func: Callable[..., Any],
    open_span: Callable[[tuple[Any, ...], dict[str, Any]], Any],
) -> Callable[..., Any]:
    """Wrap a function so each call opens a fresh span.

    Sync and async targets are handled separately. Wrapping a coroutine function
    with a plain synchronous wrapper would time the creation of the coroutine
    rather than its execution, producing a span that always reports microseconds.
    """
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with open_span(args, kwargs):
                return await func(*args, **kwargs)

        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with open_span(args, kwargs):
            return func(*args, **kwargs)

    return wrapper


def model(
    provider: str, model: str, operation: str = "chat"
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator form, for a function that always talks to one model."""

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        return _wrap(func, lambda _args, _kwargs: model_span(provider, model, operation))

    return decorate


def tool(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator form for a tool.

    Unlike a provider, a tool function's name genuinely is fixed at import time,
    so the decorator is the natural shape here. Call arguments are fingerprinted
    automatically, which is what makes M3.2 loop detection work on decorated
    tools without the author doing anything.
    """

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        return _wrap(
            func,
            lambda args, kwargs: tool_span(name, args={"args": args, "kwargs": kwargs}),
        )

    return decorate
