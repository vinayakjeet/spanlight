from __future__ import annotations

import pathlib

from spanlight.attributes import CONTRACT

README = pathlib.Path(__file__).resolve().parents[2] / "README.md"
HEADER = "| Attribute | Class | What it reveals |"
CLASSES = {"safe", "hashed", "derived", "opt-in"}


def _classified() -> dict[str, str]:
    """Pull the threat-model table out of the README."""
    lines = README.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(HEADER))

    rows = {}
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.split("|")[1:-1]]
        rows[cells[0].strip("`")] = cells[1]
    return rows


def test_every_exported_attribute_is_classified() -> None:
    """M5.5's acceptance. An attribute that reaches Grafana without anyone having
    decided what it reveals is the whole failure mode: the leak found in M5.4 was
    beside an attribute that had been thought about carefully."""
    missing = set(CONTRACT) - set(_classified())
    assert not missing, (
        f"{sorted(missing)} is exported but not classified in the README threat "
        "model. Decide what it reveals before shipping it."
    )


def test_nothing_is_classified_that_is_not_exported() -> None:
    """The other direction. A row for an attribute nobody emits is a claim about
    a guarantee that is not being kept, because nothing tests it."""
    extra = set(_classified()) - set(CONTRACT)
    assert not extra, f"{sorted(extra)} is classified but never emitted"


def test_every_class_is_one_of_the_four() -> None:
    unknown = set(_classified().values()) - CLASSES
    assert not unknown, f"unrecognised classification: {sorted(unknown)}"


def test_the_table_was_actually_found() -> None:
    """Guards the three tests above, all of which compare against whatever the
    parser returns. A renamed heading would leave it empty and every check would
    pass while checking nothing."""
    assert len(_classified()) >= 15


def test_the_fingerprint_is_the_only_hashed_attribute() -> None:
    """If a second one appears, the claim that everything user-supplied goes
    through one salted digest needs re-examining rather than extending."""
    hashed = {name for name, cls in _classified().items() if cls == "hashed"}
    assert hashed == {"spanlight.tool.args_fingerprint"}
