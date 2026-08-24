"""The environment a claim was tested under, printed as the README row.

    uv run python bench/tested_with.py

Nothing GenAI in the OpenTelemetry conventions is Stable, and a minor SDK or
convention bump can rename an attribute out from under every dashboard querying
it. The defense is pinning plus this table: dated rows recording which
Spanlight, which OTel SDK, which convention generation and which interpreter
produced the traces behind each published number. A reader who cannot reproduce
a number deserves to see what it ran on.

Paste the printed row into the README's Tested with section; the drift test
there holds the newest row to the code, so bumping the semconv pin without
touching the table fails CI rather than quietly mislabelling new traces.
"""

from __future__ import annotations

import datetime
import importlib.metadata
import platform

from spanlight.attributes import SEMCONV_VERSION, semconv_generation


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _demo_stack() -> str:
    """The demo agent's own dependencies, folded into one cell."""
    return "/".join(
        f"{name} {_version(name)}" for name in ("fastapi", "uvicorn", "httpx")
    )


def current_row(note: str) -> tuple[str, ...]:
    """One table row for the environment running right now."""
    today = datetime.date.today().isoformat()
    return (
        today,
        _version("spanlight"),
        _demo_stack(),
        _version("opentelemetry-sdk"),
        semconv_generation(),
        f"{platform.python_implementation()} {platform.python_version()}",
        f"{platform.system()} {platform.machine()}",
        note,
    )


def main() -> None:
    print(f"Pinned gen_ai convention revision: {SEMCONV_VERSION}")
    print(f"Active generation: {semconv_generation()}")
    print()
    header = (
        "| Date | Spanlight | Demo stack | OTel SDK"
        " | gen_ai semconv | Python | Host | Workload |"
    )
    print(header)
    print("|---|---|---|---|---|---|---|---|")
    print("| " + " | ".join(current_row("suite, demo agent")) + " |")


if __name__ == "__main__":
    main()
