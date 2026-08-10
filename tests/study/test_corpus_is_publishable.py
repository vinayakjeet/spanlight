from __future__ import annotations

import json
import pathlib

import pytest

from study.analyse import CORPUS
from study.collect import PROMPTS

MANIFEST = pathlib.Path(__file__).resolve().parents[2] / "study" / "corpus_manifest.json"

# Every attribute the corpus is allowed to carry as free-ish text, and the widest
# value each one legitimately needs. A ticket is 22 to 34 characters, so anything
# with room for one is a place a prompt could hide.
STRING_ATTRIBUTES = {
    "gen_ai.system": 16,
    "gen_ai.operation.name": 16,
    "gen_ai.response.model": 48,
    "spanlight.session.id": 32,
    "spanlight.detection": 32,
    "shipgate.rubric_version": 8,
    "shipgate.verdict": 8,
    "shipgate.item_id": 8,
    "shipgate.slices": 32,
    "runner": 16,
    "runner_fingerprint": 48,
    "target": 32,
    "dataset_hash": 16,
    "spanlight.detection.type": 32,
}

# Prefixes the free tiers in QUOTAS.md issue. A corpus is a file people copy into
# issues and gists, which is where a key that was only ever in .env ends up.
CREDENTIAL_PREFIXES = ("gsk_", "AIza", "glc_", "sk-", "Bearer ", "Basic ")


@pytest.fixture(scope="module")
def spans() -> list[dict]:
    if not CORPUS.exists():
        pytest.skip(f"{CORPUS} not collected")
    with CORPUS.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def values(spans: list[dict]):
    for span in spans:
        yield from span["attributes"].items()
        for event in span["events"]:
            # Event attributes are already namespaced by the emitter, so they go
            # through the same allowlist rather than a parallel one.
            yield from event["attributes"].items()


def test_no_ticket_text_survives_anywhere(spans: list[dict]) -> None:
    """The canary, run against the published artefact rather than the emitted
    span. M5.4 proves the library does not leak a prompt; this proves the file
    about to be pushed to a public repo does not contain one, which is a
    different claim and the one a reader cares about."""
    raw = CORPUS.read_text(encoding="utf-8")

    assert not [ticket for ticket in PROMPTS if ticket in raw]


def test_every_string_attribute_is_known_and_short(spans: list[dict]) -> None:
    """An allowlist catches the leaks you predicted. The length bound catches the
    attribute somebody adds later that carries a reply, a stack trace, or an
    error message, none of which fit in the widths above."""
    for key, value in values(spans):
        if not isinstance(value, str):
            continue
        assert key in STRING_ATTRIBUTES, f"{key} is not on the publication allowlist"
        assert len(value) <= STRING_ATTRIBUTES[key], f"{key} is {len(value)} chars"


def test_no_credential_shaped_value(spans: list[dict]) -> None:
    for key, value in values(spans):
        if isinstance(value, str):
            assert not value.startswith(CREDENTIAL_PREFIXES), key


def test_the_manifest_records_the_salt_without_publishing_it() -> None:
    """Fingerprints have to be comparable across processes, so the salt is pinned
    for collection. Publishing it would let a reader rebuild the fingerprint of
    any tool call they can guess the arguments of, which is the whole point of
    hashing them."""
    if not MANIFEST.exists():
        pytest.skip("corpus not collected")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["salt_set"] is True
    assert "salt" not in json.dumps(manifest).replace('"salt_set"', "")


def test_the_manifest_pins_the_taxonomy_that_was_pre_registered() -> None:
    """The pre-registration is worth nothing if the corpus cannot be tied to the
    taxonomy that existed before it. A reader checks this by hashing
    study/taxonomy.md themselves and comparing."""
    if not MANIFEST.exists():
        pytest.skip("corpus not collected")
    import hashlib

    taxonomy = pathlib.Path(__file__).resolve().parents[2] / "study" / "taxonomy.md"
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))["taxonomy_sha256"]

    assert hashlib.sha256(taxonomy.read_bytes()).hexdigest() == recorded


def test_the_corpus_holds_the_sessions_the_study_reports_on(spans: list[dict]) -> None:
    """Guards the other direction: a truncated or partially copied corpus would
    still pass every leak check above and would quietly change every number in
    the study."""
    items = [s for s in spans if s["name"] == "shipgate.item"]

    assert len(items) == 500
