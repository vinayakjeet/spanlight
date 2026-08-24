from __future__ import annotations

import pathlib
import re

import yaml

from spanlight.attributes import (
    CONTRACT,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    SEMCONV_VERSION_ATTRIBUTE,
)

CONFIG = pathlib.Path(__file__).resolve().parents[1] / "collector" / "config.yaml"

# The resource attribute lives on the resource, which the redaction processor
# never touches; every other emitted name must survive its allowlist.
RESOURCE_ONLY = {SEMCONV_VERSION_ATTRIBUTE}

# Content attributes are the one deliberate omission from the allowlist. An
# operator who wants prompt bodies exported has to enable capture at the
# library AND add these keys here: two conscious steps in two files, because a
# single stray environment variable should not be able to turn a redacting
# collector into a prompt pipeline.
CONTENT_OPT_IN = {
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
}


def _load() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _raw() -> str:
    return CONFIG.read_text(encoding="utf-8")


def test_the_traces_pipeline_runs_each_defence_in_order() -> None:
    """Normalise before redaction: OpenInference and OpenLLMetry key names are
    not on the allowlist, so a collector that redacted first would strip the
    very attributes normalisation was about to rename, and do it silently.
    Sampling runs last because its decision should see the spans as they will
    be stored, not as they arrived."""
    traces = _load()["service"]["pipelines"]["traces"]
    assert traces["processors"] == [
        "memory_limiter",
        "genainormalizer",
        "redaction",
        "tail_sampling",
    ]


def test_the_normalizer_covers_both_foreign_dialects() -> None:
    """OpenInference and OpenLLMetry between them instrument the 60+ frameworks
    the brief cares about. A config naming only one would quietly leave the
    other's spans in their own vocabulary, and a fleet dashboard reading
    gen_ai.* would render them as nothing."""
    normalizer = _load()["processors"]["genainormalizer"]
    selection = normalizer.get("sources") or normalizer.get("profiles")
    assert selection is not None, (
        "genainormalizer names neither `sources` nor `profiles`; contrib has "
        "renamed the field again and this config predates it"
    )
    assert {"openinference", "openllmetry"} <= set(selection)
    assert normalizer["remove_originals"] is True


def test_every_emitted_attribute_survives_the_redaction_allowlist() -> None:
    """The cross-check that makes the allowlist maintainable. Spanlight owns one
    mapping module for names; when a rename lands there, this test fails until
    the collector agrees, instead of production dropping an attribute nobody
    noticed was no longer allowed."""
    allowed = set(_load()["processors"]["redaction"]["allowed_keys"])
    missing = (CONTRACT - RESOURCE_ONLY - CONTENT_OPT_IN) - allowed
    assert not missing, (
        f"{sorted(missing)} is emitted but blocked by the collector allowlist. "
        "Add it or accept that those spans arrive without it."
    )


def test_content_attributes_stay_blocked_by_default() -> None:
    assert CONTENT_OPT_IN.isdisjoint(
        set(_load()["processors"]["redaction"]["allowed_keys"])
    ), (
        "the content attributes joined the default allowlist. That is a policy "
        "decision, and it should have been made consciously rather than by "
        "adding a key while silencing this test."
    )


def test_blocked_values_are_patterns_not_examples() -> None:
    blocked = _load()["processors"]["redaction"]["blocked_values"]
    assert len(blocked) >= 3
    for pattern in blocked:
        re.compile(pattern)


def test_tail_sampling_keeps_every_error_and_slow_session() -> None:
    """The brief's sampling requirement, stated where it is actually
    implemented. 100% on errors and slow turns is the override a head sampler
    structurally cannot make; if these policies weaken, the README claim goes
    with them."""
    policies = {
        p["name"]: p for p in _load()["processors"]["tail_sampling"]["policies"]
    }
    assert policies["errors"]["type"] == "status_code"
    assert policies["errors"]["status_code"]["status_codes"] == ["ERROR"]
    assert policies["slow-turns"]["latency"]["threshold_ms"] > 0

    routine = policies["routine-sessions"]["probabilistic"]["sampling_percentage"]
    assert 0 < routine < 100


def test_no_credential_is_written_into_the_file() -> None:
    raw = _raw()
    assert "${env:GRAFANA_OTLP_ENDPOINT}" in raw
    assert "${env:GRAFANA_AUTHORIZATION}" in raw
    # A literal Basic prefix here would mean somebody pasted half a token.
    assert "Basic" not in raw
