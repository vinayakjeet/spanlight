from __future__ import annotations

import pathlib

from spanlight.attributes import CONTRACT, EVENT_CONTRACT

SPEC = pathlib.Path(__file__).resolve().parents[2] / "SPEC.md"
HEADER = "| Attribute | Type | Example | On |"
EVENT_HEADER = "| Event attribute | Type | Example | On |"


def _documented(header: str) -> set[str]:
    """Pull the attribute names out of one SPEC table."""
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(header))

    names = set()
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        names.add(line.split("|")[1].strip().strip("`"))
    return names


def _documented_attributes() -> set[str]:
    return _documented(HEADER)


def _documented_event_attributes() -> set[str]:
    return _documented(EVENT_HEADER)


def test_every_documented_attribute_has_a_constant() -> None:
    missing = _documented_attributes() - set(CONTRACT)
    assert not missing, (
        f"SPEC.md documents {sorted(missing)} with no constant in attributes.py. "
        "A dashboard written against the documented name would return nothing."
    )


def test_every_constant_is_documented() -> None:
    undocumented = set(CONTRACT) - _documented_attributes()
    assert not undocumented, (
        f"attributes.py emits {sorted(undocumented)} which SPEC.md does not document. "
        "An attribute nobody knows about is an attribute nobody queries."
    )


def test_every_documented_event_attribute_has_a_constant() -> None:
    missing = _documented_event_attributes() - set(EVENT_CONTRACT)
    assert not missing, (
        f"SPEC.md documents event attributes {sorted(missing)} with no constant "
        "in attributes.py."
    )


def test_every_event_constant_is_documented() -> None:
    undocumented = set(EVENT_CONTRACT) - _documented_event_attributes()
    assert not undocumented, (
        f"attributes.py emits event attributes {sorted(undocumented)} which "
        "SPEC.md does not document."
    )


def test_the_two_tables_do_not_overlap() -> None:
    """A name appearing as both a span attribute and an event attribute would
    make every query against it ambiguous, and the two drift tests would each
    consider it documented by the other's table."""
    assert not (set(CONTRACT) & set(EVENT_CONTRACT))


def test_the_table_was_actually_found() -> None:
    """Guards the two tests above.

    Both compare against whatever `_documented_attributes` returns. If the SPEC
    table were renamed or moved, the parser could quietly return an empty set
    and the drift checks would pass while checking nothing. That is the shape of
    bug that let ShipGate ship a gate incapable of failing.
    """
    assert len(_documented_attributes()) >= 15
    assert len(_documented_event_attributes()) >= 5
