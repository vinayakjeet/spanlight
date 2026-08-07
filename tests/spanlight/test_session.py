from __future__ import annotations

import asyncio

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight.attributes import SESSION_ID


def test_generates_an_id_when_none_is_given() -> None:
    with spanlight.session() as session_id:
        assert session_id
        assert spanlight.current_session_id() == session_id


def test_accepts_a_supplied_id() -> None:
    with spanlight.session("gate-run-42") as session_id:
        assert session_id == "gate-run-42"


def test_every_span_kind_carries_the_session(spans: InMemorySpanExporter) -> None:
    with spanlight.session("run-1"):
        with spanlight.model_span(provider="mock"):
            pass
        with spanlight.tool_span("search"):
            pass
        with spanlight.retrieval_span("schemes"):
            pass

    finished = spans.get_finished_spans()
    # Four, not three: the session is itself a span, and it carries the id too.
    assert len(finished) == 4
    assert {span.attributes[SESSION_ID] for span in finished} == {"run-1"}


def test_a_session_is_one_trace_with_the_steps_as_children(
    spans: InMemorySpanExporter,
) -> None:
    """The waterfall SPEC promises, which did not exist while `session()` was
    only a context variable. Every step was then a parentless root, so a
    three-step run reached Tempo as three unrelated traces sharing an
    attribute, and S8's guarantee that an exported session keeps its children
    was true only because it never had any."""
    with spanlight.session("run-2"):
        with spanlight.model_span(provider="mock"):
            pass
        with spanlight.tool_span("search"):
            pass

    finished = spans.get_finished_spans()
    assert len({span.context.trace_id for span in finished}) == 1

    (root,) = [span for span in finished if span.parent is None]
    assert root.name == "session"
    children = [span for span in finished if span is not root]
    assert {span.parent.span_id for span in children} == {root.context.span_id}


def test_nested_sessions_nest_their_spans(spans: InMemorySpanExporter) -> None:
    with spanlight.session("outer"), spanlight.session("inner"), spanlight.tool_span("search"):
        pass

    by_name = {span.name: span for span in spans.get_finished_spans()}
    outer, inner = (span for span in spans.get_finished_spans() if span.name == "session")
    tool = by_name["tool search"]
    assert tool.attributes[SESSION_ID] == "inner"
    assert {outer.attributes[SESSION_ID], inner.attributes[SESSION_ID]} == {
        "outer",
        "inner",
    }


def test_a_span_outside_a_session_has_no_session_id(spans: InMemorySpanExporter) -> None:
    """Absent rather than a placeholder. An id like `unknown` would group every
    unsessioned span in the corpus into one enormous fake session."""
    with spanlight.model_span(provider="mock"):
        pass

    (span,) = spans.get_finished_spans()
    assert SESSION_ID not in span.attributes


def test_nesting_restores_the_outer_session() -> None:
    with spanlight.session("outer"):
        with spanlight.session("inner"):
            assert spanlight.current_session_id() == "inner"
        assert spanlight.current_session_id() == "outer"
    assert spanlight.current_session_id() is None


async def test_concurrent_sessions_do_not_leak_into_each_other(
    spans: InMemorySpanExporter,
) -> None:
    """The reason this is a ContextVar and not a module global.

    A global would hand every span the id of whichever session started most
    recently, and an agent process runs many sessions at once. The staggered
    sleeps force the two sessions to interleave rather than run back to back.
    """

    async def run(session_id: str, delay: float) -> None:
        with spanlight.session(session_id):
            await asyncio.sleep(delay)
            with spanlight.model_span(provider="mock"):
                await asyncio.sleep(delay)

    await asyncio.gather(run("a", 0.02), run("b", 0.01), run("c", 0.03))

    by_session = {span.attributes[SESSION_ID] for span in spans.get_finished_spans()}
    assert by_session == {"a", "b", "c"}
