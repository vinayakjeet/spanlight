from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight._content import CAPTURE_CONTENT_VAR, capture_enabled, record_content
from spanlight.attributes import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
)

CANARY = "canary-ZQ74X1-user@example.com"

SYSTEM = [{"type": "text", "content": "You score items."}]
INPUT = [
    {
        "role": "user",
        "parts": [{"type": "text", "content": f"score item {CANARY}"}],
    }
]
OUTPUT = [
    {
        "role": "assistant",
        "parts": [{"type": "text", "content": "pass"}],
        "finish_reason": "stop",
    }
]


def _set_mode(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(CAPTURE_CONTENT_VAR, raising=False)
    else:
        monkeypatch.setenv(CAPTURE_CONTENT_VAR, value)


def test_capture_is_off_with_no_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, None)
    assert capture_enabled() is False


@pytest.mark.parametrize("mode", ["NO_CONTENT", "EVENT_ONLY", "", "yes", "span_only "])
def test_only_the_span_modes_turn_capture_on(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """`EVENT_ONLY` stays off because Spanlight emits no log events; letting it
    silently mean SPAN_ONLY would make the variable mean something different
    here than it means for every other instrumentation reading it. Case and
    padding are tolerated the way the Python contrib tooling tolerates them."""
    _set_mode(monkeypatch, mode)
    assert capture_enabled() is (mode.strip().upper() in {"SPAN_ONLY", "SPAN_AND_EVENT"})


def test_disabled_capture_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    """The default path, and the one every portfolio deployment runs. A canary
    handed straight to `record_content` must leave nothing behind: the call site
    staying in place unconditionally is only safe if off really means off."""
    _set_mode(monkeypatch, None)
    with spanlight.model_span(provider="mock"):
        written = record_content(
            system_instructions=SYSTEM,
            input_messages=INPUT,
            output_messages=OUTPUT,
        )

    assert written is False
    (span,) = spans.get_finished_spans()
    for name in (
        GEN_AI_SYSTEM_INSTRUCTIONS,
        GEN_AI_INPUT_MESSAGES,
        GEN_AI_OUTPUT_MESSAGES,
    ):
        assert name not in span.attributes
    assert CANARY not in json.dumps(
        {k: str(v) for k, v in span.attributes.items()}
    )


def test_span_only_writes_the_convention_names_as_json_strings(
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    """The convention allows message attributes on spans to be JSON strings
    where structured values are unsupported, which is the case here: span
    attributes are primitives. The strings must parse back into the shape the
    caller passed, parts and all, or a collector filtering on that shape will
    silently drop them."""
    _set_mode(monkeypatch, "SPAN_ONLY")
    with spanlight.model_span(provider="mock"):
        written = record_content(
            system_instructions=SYSTEM,
            input_messages=INPUT,
            output_messages=OUTPUT,
        )

    assert written is True
    (span,) = spans.get_finished_spans()
    instructions = json.loads(span.attributes[GEN_AI_SYSTEM_INSTRUCTIONS])
    inputs = json.loads(span.attributes[GEN_AI_INPUT_MESSAGES])
    outputs = json.loads(span.attributes[GEN_AI_OUTPUT_MESSAGES])

    assert instructions == SYSTEM
    assert inputs[0]["parts"][0]["content"] == f"score item {CANARY}"
    assert outputs[0]["finish_reason"] == "stop"


def test_partial_capture_writes_only_what_was_given(
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    _set_mode(monkeypatch, "SPAN_ONLY")
    with spanlight.model_span(provider="mock"):
        record_content(input_messages=INPUT)

    (span,) = spans.get_finished_spans()
    assert GEN_AI_INPUT_MESSAGES in span.attributes
    assert GEN_AI_SYSTEM_INSTRUCTIONS not in span.attributes
    assert GEN_AI_OUTPUT_MESSAGES not in span.attributes


def test_record_content_outside_any_span_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runs without the `spans` fixture, so there is no provider at all. The
    current span is a non-recording one whose `attributes` do not exist; writing
    to it must neither raise nor pretend to have worked."""
    _set_mode(monkeypatch, "SPAN_ONLY")
    assert record_content(input_messages=INPUT) is False


def test_a_dropped_span_captures_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same surface from the other side: a sampled-out session produces a
    `NonRecordingSpan`, and content capture must follow every other attribute
    write in treating that as gone rather than crashing."""
    _set_mode(monkeypatch, "SPAN_AND_EVENT")
    with spanlight.session():
        assert record_content(system_instructions=SYSTEM) is False
