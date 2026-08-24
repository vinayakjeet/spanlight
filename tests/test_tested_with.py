from __future__ import annotations

import datetime
import pathlib
import tomllib

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"
PYPROJECT = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
HEADER = (
    "| Date | Spanlight | Demo stack | OTel SDK | gen_ai semconv | Python | Host | Workload |"
)


def _rows() -> list[list[str]]:
    lines = README.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(HEADER))
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) != 8:
            raise AssertionError(f"tested-with row has {len(cells)} cells: {line}")
        rows.append(cells)
    return rows


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".")[:3])


def test_the_table_exists_and_has_rows() -> None:
    """Guards the tests below, all of which compare against whatever the parser
    finds. A renamed header would leave it empty and every check would pass
    while checking nothing."""
    assert len(_rows()) >= 1


def test_every_row_is_dated() -> None:
    """The table's whole value is that a reader can tell which claims were made
    under which environment. An undated row is a row nobody can trust or
    reproduce, which is worse than no row."""
    for row in _rows():
        try:
            datetime.date.fromisoformat(row[0])
        except ValueError as exc:
            raise AssertionError(f"row is not dated: {' | '.join(row)}") from exc


def test_the_newest_row_matches_the_code() -> None:
    """The drift check. Bump `SEMCONV_VERSION` or the package version without
    re-running the suite under the new pin and this fails, so the newest row can
    never quietly describe an environment that no longer exists. Rows below the
    newest are history and stay as they were written."""
    latest = _rows()[0]
    from spanlight.attributes import SEMCONV_VERSION

    project = _pyproject()["project"]

    assert latest[1] == project["version"], (
        "the newest tested-with row names Spanlight "
        f"{latest[1]} but pyproject says {project['version']}"
    )
    assert latest[4].split("+")[0] == SEMCONV_VERSION, (
        "the newest tested-with row was collected under gen_ai semconv "
        f"{latest[4].split('+')[0]} but attributes.py pins {SEMCONV_VERSION}"
    )


def test_the_sdk_never_falls_below_the_declared_floor() -> None:
    """`opentelemetry-sdk>=1.28` is what the library promises to need; a row
    claiming less than that would mean the suite passed on something the
    dependency contract forbids."""
    floor = next(
        dep.split(">=")[1]
        for dep in _pyproject()["project"]["dependencies"]
        if dep.startswith("opentelemetry-sdk")
    )
    latest = _rows()[0]
    assert _version_tuple(latest[3]) >= _version_tuple(floor), (
        f"newest row records OTel SDK {latest[3]}, below the declared >= {floor}"
    )
