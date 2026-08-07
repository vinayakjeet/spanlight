from __future__ import annotations

import asyncio

import pytest

import spanlight


@pytest.mark.asyncio
async def test_concurrent_model_spans_have_correct_parents(
    spans,  # noqa: ARG001
) -> None:
    """Interleaved async spans must record their true parent, not a leaked one.

    A naive implementation reads the current span at start time:

        def model_span(provider):
            parent = tracer.current_span()
            with tracer.start_as_current_span(...) as span:
                span.parent = parent  # Wrong time to capture it

    Under concurrency, this leaks the first task's span into the second:

        Task 1: model_span A starts
                 parent = None
        Task 2: model_span B starts
                 parent = A  # Captured the wrong task's current span
        Task 1: model_span A ends
        Task 2: model_span B ends

    OpenTelemetry uses ContextVar to store the current span, which is task-local
    in asyncio. Each task sees its own value, and the parent is correctly None
    for both. This test proves the mechanism works by running 20 interleaved
    calls with staggered sleeps and asserting each span's parent id.
    """
    span_ids: dict[str, int] = {}

    async def task(task_id: int, delay: float) -> None:
        # Each task waits a different amount, forcing interleaving.
        await asyncio.sleep(delay)
        with spanlight.model_span(provider="mock") as span:
            # Capture the span id now, inside the span.
            ctx = span.get_span_context()
            span_ids[f"task_{task_id}"] = ctx.span_id
            # Longer tasks mean more interleaving.
            await asyncio.sleep(0.01 * task_id)

    # Fire 20 tasks with staggered sleeps. The stagger forces interleaving:
    # some will start while others are sleeping inside their span.
    await asyncio.gather(
        *(task(i, 0.001 * (i % 3)) for i in range(20)),
    )

    # Check the exported spans. Each task's span should have no parent
    # (parent_span_id is INVALID_SPAN_ID, which is 0), even though they ran
    # concurrently. If the implementation naively captured "current span at start
    # time", many would incorrectly claim another task's span as their parent.
    exported = spans.get_finished_spans()
    assert len(exported) >= 20, f"Expected at least 20 spans, got {len(exported)}"

    # Find the spans we just created (they have name "chat" from model_span).
    task_spans = [s for s in exported if s.name == "chat"]
    assert len(task_spans) == 20, f"Expected 20 task spans, got {len(task_spans)}"

    # Each task span should be a root (no parent). In OpenTelemetry, root spans
    # have parent = None.
    for span in task_spans:
        assert span.parent is None, (
            f"Span {span.context.span_id} has parent {span.parent}; "
            "concurrency may be leaking spans"
        )


@pytest.mark.asyncio
async def test_nested_async_spans_have_correct_lineage(
    spans,  # noqa: ARG001
) -> None:
    """A parent span started in an async context retains correct lineage.

    When an async parent task spawns child tasks, the children should not see
    the parent as their current span, only as their parent. This is true because
    ContextVar is task-local, so each child task starts with no current span.
    The parent is recorded by OpenTelemetry's tracing machinery reading the span
    from the context at the time the child span is created.
    """
    parent_span_id = None
    child_trace_ids: list[int] = []

    async def child_task(task_id: int) -> None:  # noqa: ARG001
        with spanlight.model_span(provider="mock") as span:
            ctx = span.get_span_context()
            child_trace_ids.append(ctx.trace_id)

    with spanlight.model_span(provider="mock") as parent:
        parent_ctx = parent.get_span_context()
        parent_span_id = parent_ctx.span_id

        # Spawn three child tasks. Each should inherit the trace id.
        await asyncio.gather(
            *(child_task(i) for i in range(3)),
        )

    # Three children, all in the same trace.
    assert len(child_trace_ids) == 3
    assert all(tid == child_trace_ids[0] for tid in child_trace_ids)

    # Check exported spans. Find the parent and child spans.
    exported = spans.get_finished_spans()
    parent_spans = [s for s in exported if s.context.span_id == parent_span_id]
    assert len(parent_spans) == 1

    child_spans = [
        s for s in exported
        if s.parent is not None and s.parent.span_id == parent_span_id
    ]
    assert len(child_spans) == 3, (
        f"Expected 3 children of parent {parent_span_id}, got {len(child_spans)}"
    )
