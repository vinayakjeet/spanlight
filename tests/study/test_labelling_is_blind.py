from __future__ import annotations

from study.label import CLASSES, blind, sample

# A span as the corpus stores it, carrying everything a detector wrote.
DETECTED = {
    "name": "shipgate.item",
    "status": "OK",
    "duration_ms": 1200.0,
    "attributes": {
        "spanlight.session.id": "abc",
        "spanlight.detection": "cost_ceiling",
        "spanlight.detection.threshold_usd": 0.00005,
        "gen_ai.response.model": "llama-3.3-70b-versatile",
    },
    "events": [
        {"name": "spanlight.detection", "attributes": {"type": "cost_ceiling"}},
        {"name": "shipgate.cache_miss", "attributes": {}},
    ],
}


def test_the_detection_attribute_is_stripped() -> None:
    """If the labeller can see the verdict, the labels drift toward agreeing with
    it, and the precision number afterwards measures how persuasive the detector
    was rather than whether it was right."""
    assert "spanlight.detection" not in blind(DETECTED)["attributes"]


def test_every_detection_attribute_is_stripped_not_just_the_first() -> None:
    """`spanlight.detection.threshold_usd` gives away both that something fired
    and what it was measured against."""
    surviving = blind(DETECTED)["attributes"]
    assert not [k for k in surviving if k.startswith("spanlight.detection")]


def test_the_detection_event_is_stripped() -> None:
    """The event name is the verdict spelled out, so leaving it would give the
    game away just as completely as the attribute."""
    names = [e["name"] for e in blind(DETECTED)["events"]]
    assert "spanlight.detection" not in names


def test_the_host_s_own_events_survive() -> None:
    """Blinding must not empty the span. A labeller with nothing to read cannot
    label, and would skip everything."""
    names = [e["name"] for e in blind(DETECTED)["events"]]
    assert names == ["shipgate.cache_miss"]


def test_what_is_needed_to_label_survives() -> None:
    """The other half of blindness: strip the verdict, keep the evidence."""
    surviving = blind(DETECTED)["attributes"]
    assert surviving["gen_ai.response.model"] == "llama-3.3-70b-versatile"
    assert blind(DETECTED)["duration_ms"] == 1200.0
    assert blind(DETECTED)["status"] == "OK"


def test_the_original_span_is_not_mutated() -> None:
    """The corpus is read once and analysed twice. If blinding edited in place,
    the precision calculation afterwards would run against sessions whose
    verdicts had been deleted, and every detector would score zero."""
    blind(DETECTED)

    assert DETECTED["attributes"]["spanlight.detection"] == "cost_ceiling"
    assert len(DETECTED["events"]) == 2


def test_the_sample_is_reproducible_from_the_seed() -> None:
    """The seed is fixed in taxonomy.md before collection, so which sessions get
    labelled is not a choice made after seeing them."""
    runs = {f"session-{n}": {} for n in range(500)}

    assert sample(runs) == sample(runs)


def test_the_sample_does_not_depend_on_corpus_order() -> None:
    """Sessions are sorted before drawing. Without that the sample would depend on
    the order spans happened to be written, which is collection timing rather
    than anything reproducible."""
    forward = {f"session-{n}": {} for n in range(500)}
    backward = {f"session-{n}": {} for n in reversed(range(500))}

    assert sample(forward) == sample(backward)


def test_every_taxonomy_class_is_reachable() -> None:
    """A class nobody can select is a class that will read as absent from the
    corpus rather than as unlabellable."""
    codes = {code for code, _ in CLASSES.values()}

    assert codes == {"A1", "A2", "A3", "A4", "A5", "A6", "A7", "SKIP"}
