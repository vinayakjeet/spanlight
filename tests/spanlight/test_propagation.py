from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight.attributes import SESSION_ID


def test_headers_carry_the_trace_and_the_session(spans: InMemorySpanExporter) -> None:
    with spanlight.session("run-7"):
        carrier = spanlight.headers()

    assert carrier["traceparent"].startswith("00-")
    assert carrier["baggage"] == f"{SESSION_ID}=run-7"


def test_headers_carry_no_traceparent_when_tracing_is_off() -> None:
    """With no provider configured the current span context is invalid, and an
    invalid context must not be propagated: the far side would join a trace that
    does not exist. Deliberately runs without the `spans` fixture, which is what
    makes the SDK absent."""
    with spanlight.session("run-7"):
        carrier = spanlight.headers()

    assert "traceparent" not in carrier
    assert carrier["baggage"] == f"{SESSION_ID}=run-7"


def test_headers_outside_a_session_carry_no_baggage() -> None:
    """Absent rather than a placeholder, for the same reason the span attribute
    is: an id like `unknown` would join every unsessioned run in the corpus into
    one enormous fake session."""
    carrier = spanlight.headers()

    assert "baggage" not in carrier


def test_the_far_side_joins_the_caller_trace(spans: InMemorySpanExporter) -> None:
    """SPEC S7, HTTP half. One trace id across two hops, with the callee's
    session span parented to the caller's."""
    with spanlight.session("caller"):
        carrier = spanlight.headers()

    # The caller's context is gone before the callee starts, which is the whole
    # point: in another process it would never have existed. Nesting the callee
    # inside the caller instead would let it inherit the ambient context, and the
    # trace ids would match whether or not the headers were read at all. That
    # version of this test passed against an implementation that never attached
    # the extracted context.
    with spanlight.session(headers=carrier) as callee_id:
        assert callee_id == "caller"

    caller, callee = spans.get_finished_spans()
    assert callee.context.trace_id == caller.context.trace_id
    assert callee.parent.span_id == caller.context.span_id


def test_an_upstream_session_id_is_adopted_not_regenerated(
    spans: InMemorySpanExporter,
) -> None:
    """An agent that calls a second service is one logical run. A fresh id per
    hop would split it, and the study would score one two-service failure as two
    unrelated short sessions."""
    with spanlight.session("upstream"):
        carrier = spanlight.headers()

    with spanlight.session(headers=carrier) as adopted:
        pass

    assert adopted == "upstream"
    assert {s.attributes[SESSION_ID] for s in spans.get_finished_spans()} == {
        "upstream"
    }


def test_an_explicit_id_beats_the_inbound_one(spans: InMemorySpanExporter) -> None:
    with spanlight.session("upstream"):
        carrier = spanlight.headers()

    with spanlight.session("chosen", headers=carrier) as resolved:
        pass

    assert resolved == "chosen"


def test_headers_without_a_traceparent_start_a_new_trace(
    spans: InMemorySpanExporter,
) -> None:
    """A visitor hitting the demo URL from a browser sends no traceparent. That
    has to be an ordinary new session, not an error and not a span orphaned
    under a context that does not exist."""
    with (
        spanlight.session(headers={"user-agent": "curl/8.4.0"}) as session_id,
        spanlight.tool_span("search"),
    ):
        pass

    assert session_id
    (root,) = [s for s in spans.get_finished_spans() if s.parent is None]
    assert root.name == "session"


def test_a_malformed_traceparent_does_not_raise(spans: InMemorySpanExporter) -> None:
    """Inbound headers are attacker-controlled. Instrumentation that raises on a
    bad one turns a malformed header into an outage."""
    with spanlight.session(headers={"traceparent": "not-a-traceparent"}) as session_id:
        pass

    assert session_id
