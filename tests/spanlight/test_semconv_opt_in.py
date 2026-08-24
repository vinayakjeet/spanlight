from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import spanlight
from spanlight.attributes import (
    GEN_AI_PROVIDER_NAME,
    GEN_AI_SYSTEM,
    SEMCONV_VERSION,
    SESSION_ID,
    STABILITY_OPT_IN,
    experimental_opt_in,
    resolve,
    semconv_generation,
)


def _set_opt_in(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(STABILITY_OPT_IN, raising=False)
    else:
        monkeypatch.setenv(STABILITY_OPT_IN, value)


def test_default_is_the_pinned_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_opt_in(monkeypatch, None)
    assert experimental_opt_in() is False
    assert resolve(GEN_AI_SYSTEM) == GEN_AI_SYSTEM
    assert semconv_generation() == SEMCONV_VERSION


def test_the_opt_in_switches_the_one_renamed_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one rename the development conventions have made so far:
    `gen_ai.system` became `gen_ai.provider.name`, which is now required on
    inference spans. If this assertion starts failing because the table gained
    entries, that is the mechanism working."""
    _set_opt_in(monkeypatch, "gen_ai_latest_experimental")
    assert resolve(GEN_AI_SYSTEM) == GEN_AI_PROVIDER_NAME
    assert semconv_generation() == f"{SEMCONV_VERSION}+gen_ai_latest_experimental"


def test_names_outside_the_rename_table_are_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spanlight's own attributes are not convention names and must never move
    under an opt-in meant for GenAI ones. A dashboard querying
    `spanlight.session.id` cannot be allowed to break because a host opted into
    the latest GenAI names."""
    _set_opt_in(monkeypatch, "gen_ai_latest_experimental")
    for name in (SESSION_ID, "spanlight.cost_usd", "error.type", "http.method"):
        assert resolve(name) == name


def test_the_variable_is_a_comma_separated_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spec defines the value as a list of category migrations, so a host
    opting into several at once must still land on the experimental names."""
    _set_opt_in(monkeypatch, "db_migration, gen_ai_latest_experimental ,")
    assert experimental_opt_in() is True


def test_unknown_values_do_not_enable_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The variable belongs to the ecosystem. A value meant for some other
    instrumentation is ignored here rather than treated as a reason to fail."""
    _set_opt_in(monkeypatch, "http_dupe, something_else")
    assert experimental_opt_in() is False


def test_model_spans_emit_the_experimental_name_under_the_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    _set_opt_in(monkeypatch, "gen_ai_latest_experimental")
    with spanlight.model_span(provider="groq"):
        pass

    (span,) = spans.get_finished_spans()
    assert span.attributes[GEN_AI_PROVIDER_NAME] == "groq"
    assert GEN_AI_SYSTEM not in span.attributes


def test_pinned_emission_is_unchanged_without_the_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    _set_opt_in(monkeypatch, None)
    with spanlight.model_span(provider="groq"):
        pass

    (span,) = spans.get_finished_spans()
    assert span.attributes[GEN_AI_SYSTEM] == "groq"
    assert GEN_AI_PROVIDER_NAME not in span.attributes


def test_generation_reads_the_environment_per_process() -> None:
    """A subprocess, because `experimental_opt_in` reads `os.environ` and every
    other test in this file runs in one shared interpreter where import order
    would decide the answer. The corpus was collected under the pinned
    generation; a trace exported after a host flips the variable has to say so
    on its own resource rather than inheriting whatever the process saw first."""
    code = textwrap.dedent(
        """
        import os
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental"
        from spanlight.attributes import semconv_generation
        print(semconv_generation())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().endswith("+gen_ai_latest_experimental")
