from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight._propagation import mcp_meta
from spanlight.attributes import SESSION_ID


def test_meta_carries_the_w3c_keys_unprefixed(spans: InMemorySpanExporter) -> None:
    """SEP-414 fixes the key names as `traceparent`, `tracestate` and
    `baggage`, unprefixed inside `_meta`, which is an explicit exception to
    MCP's DNS-prefixing rule for that field. A namespaced key like
    `io.modelcontextprotocol/traceparent` would correlate with nothing."""
    with spanlight.session("run-9"):
        meta = mcp_meta()

    assert meta["traceparent"].startswith("00-")
    assert meta["baggage"] == f"{SESSION_ID}=run-9"


def test_meta_carrying_no_traceparent_when_tracing_is_off() -> None:
    """An invalid context must not be propagated: the far side would join a
    trace that does not exist. Runs deliberately without the `spans` fixture."""
    with spanlight.session("run-9"):
        meta = mcp_meta()

    assert "traceparent" not in meta
    assert meta["baggage"] == f"{SESSION_ID}=run-9"


def test_one_trace_across_three_services(spans: InMemorySpanExporter) -> None:
    """The brief's cross-service target in the shape a test can hold: host to
    gateway to MCP server, each hop receiving the caller's context through
    `params._meta`.

    Every caller closes its session before the next hop opens, because in
    another process it would never have existed. The first version of the HTTP
    equivalent passed against an implementation that never read the inbound
    context at all, purely by keeping both hops inside one ambient session."""
    with spanlight.session("host-session"):
        host_meta = mcp_meta()

    with spanlight.session(headers=host_meta) as gateway_session:
        gateway_meta = mcp_meta()

    with spanlight.session(headers=gateway_meta) as server_session:
        pass

    host, gateway, server = (
        s for s in spans.get_finished_spans() if s.name == "session"
    )
    assert gateway.context.trace_id == host.context.trace_id
    assert server.context.trace_id == host.context.trace_id
    assert gateway.parent.span_id == host.context.span_id
    assert server.parent.span_id == gateway.context.span_id
    assert {gateway_session, server_session} == {"host-session"}


def test_the_server_adopts_no_new_id_downstream(spans: InMemorySpanExporter) -> None:
    """Every hop carries the same `spanlight.session.id`, so the study counts a
    three-service run as one session rather than as three short ones."""
    with spanlight.session("one-run") as first:
        meta = mcp_meta()

    with spanlight.session(headers=meta):
        inner = spanlight.mcp_meta()

    with spanlight.session(headers=inner):
        pass

    carried = {
        s.attributes[SESSION_ID] for s in spans.get_finished_spans()
    }
    assert carried == {first}


def test_an_explicit_session_id_beats_the_meta_baggage(
    spans: InMemorySpanExporter,
) -> None:
    with spanlight.session("from-meta"):
        meta = mcp_meta()

    with spanlight.session("chosen", headers=meta) as resolved:
        pass

    assert resolved == "chosen"


def test_an_empty_or_missing_meta_starts_a_fresh_trace(
    spans: InMemorySpanExporter,
) -> None:
    """A request with no `_meta`, or an empty one, is an ordinary new run: not
    an error, not a span orphaned under a context that does not exist."""
    with spanlight.session(headers={}) as empty:
        pass

    with spanlight.session(headers=None) as missing:
        pass

    assert empty and missing and empty != missing
    (first, second) = [
        s for s in spans.get_finished_spans() if s.name == "session"
    ]
    assert first.parent is None and second.parent is None


def test_malformed_meta_values_do_not_raise(spans: InMemorySpanExporter) -> None:
    """`_meta` is attacker-supplied on any public endpoint. Instrumentation
    that raises on a bad value turns a malformed request into an outage."""
    broken = {"traceparent": "not-a-traceparent", "baggage": "%%%=nope"}
    with spanlight.session(headers=broken):
        pass


def test_meta_merges_into_an_existing_property_bag(
    spans: InMemorySpanExporter,
) -> None:
    """A real `params._meta` usually carries other keys already. Injection must
    add to what is there rather than hand back a bag containing only trace
    context."""
    with spanlight.session("run-3"):
        meta = mcp_meta({"progressToken": "tok-1"})

    assert meta["progressToken"] == "tok-1"
    assert "traceparent" in meta
