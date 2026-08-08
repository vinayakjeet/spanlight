"""What adopting Spanlight actually costs, in lines, per call site.

    PYTHONPATH=. uv run python bench/adoption_diff.py

The README claims three-line adoption. That is the kind of claim a project makes
about itself and never checks, so this counts it.

Counted: every statement that mentions `spanlight`, plus its import. For a
`with spanlight.model_span(...)` the header is counted and the body is not,
because the body is the code that was already there. Comments are excluded; a
line explaining why is not a line the adopter has to write.

Deliberately measures the current tree rather than a git diff. A diff attributes
whatever the commit happened to contain, and the commits here mixed
instrumentation with other work. What an adopter cares about is how much
Spanlight is sitting in their file now.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]

# Every place in this repo that adopts the library. This is Spanlight measuring
# itself, so it is the flattering half of the number; the sibling below is the
# one that counts.
SITES = {
    "llm/client.py": "chassis LLM client, instruments every provider call",
    "app/main.py": "demo app startup",
    "app/routers/agent.py": "demo agent route",
}

# The number that actually matters. ShipGate was built before Spanlight existed
# and had its own working tracing, so this is the only measurement here that is
# not the library grading its own homework. Counted when the repo is present
# beside this one, skipped otherwise, so the bench still runs from a clean clone.
SIBLING = pathlib.Path(r"d:/projects/shipgate")
SIBLING_SITES = {
    "shipgate/cli.py": "gate CLI, one session per run",
    "shipgate/runners/base.py": "item scoring, one session per item",
    "shipgate/runners/judge.py": "judge model call",
    "shipgate/runners/pairwise.py": "pairwise model call",
}


def _imported_names(tree: ast.AST) -> set[str]:
    """Names this module pulled out of Spanlight.

    Needed because `from spanlight.attributes import GEN_AI_RESPONSE_MODEL` puts
    a bare `GEN_AI_RESPONSE_MODEL` into the file, and a line using it is adoption
    cost even though the word "spanlight" is nowhere on it.
    """
    names = {"spanlight"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "spanlight"
        ):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(
                alias.asname or alias.name.split(".")[0]
                for alias in node.names
                if alias.name.startswith("spanlight")
            )
    return names


def _uses_spanlight(node: ast.AST, names: set[str]) -> bool:
    """Resolve identifiers rather than grepping the source.

    A substring match counts `app = FastAPI(title="spanlight")` as adoption,
    which is how the first version of this script inflated the demo app by a
    line and quietly made the headline number better.
    """
    return any(
        isinstance(inner, ast.Name) and inner.id in names for inner in ast.walk(node)
    )


def _span_of(node: ast.AST) -> range:
    return range(node.lineno, (getattr(node, "end_lineno", None) or node.lineno) + 1)


def adoption_lines(path: pathlib.Path) -> tuple[list[tuple[int, str]], int]:
    """What an adopter had to write: the lines, and how many statements.

    Both, because they answer different questions and only one of them is
    stable. `record_usage(...)` is six physical lines as this repo formats it and
    one line if written wide, so a line count is partly a measure of the
    formatter. The statement count is what a reader means by "three lines to
    adopt", and it is the number that survives a reformat.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = _imported_names(tree)
    lines = source.splitlines()
    found: dict[int, str] = {}
    statements = 0

    def record(node: ast.AST) -> None:
        nonlocal statements
        statements += 1
        for offset in _span_of(node):
            found[offset] = lines[offset - 1]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            segment = ast.get_source_segment(source, node) or ""
            if "spanlight" in segment:
                record(node)
            continue

        # Decorators are adoption too: `@spanlight.tool("lookup_scheme")` is the
        # whole cost of instrumenting that function. The first version missed
        # them entirely and under-counted the route.
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for decorator in node.decorator_list:
                if _uses_spanlight(decorator, names):
                    record(decorator)
            continue

        # A `with` costs its header only. The body is the code that already
        # existed and would still be there without any of this.
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                if _uses_spanlight(item.context_expr, names):
                    record(item.context_expr)
            continue

        if isinstance(node, ast.Expr | ast.Assign | ast.AnnAssign) and _uses_spanlight(
            node, names
        ):
            record(node)

    kept = sorted(
        (number, text)
        for number, text in found.items()
        if text.strip() and not text.strip().startswith("#")
    )
    return kept, statements


def main() -> None:
    print("Adoption cost by call site.\n")

    total_lines = 0
    total_statements = 0
    width = max(len(site) for site in SITES)
    for site, description in SITES.items():
        counted, statements = adoption_lines(REPO / site)
        total_lines += len(counted)
        total_statements += statements
        print(
            f"{site:{width}}  {statements:>2} statements, "
            f"{len(counted):>2} lines   {description}"
        )
        for number, text in counted:
            print(f"{'':{width}}  {number:>6}   {text.strip()}")
        print()

    sites = len(SITES)
    print(
        f"{'total':{width}}  {total_statements:>2} statements, "
        f"{total_lines:>2} lines across {sites} sites"
    )
    print(
        f"{'per site':{width}}  {total_statements / sites:>4.1f} statements, "
        f"{total_lines / sites:>4.1f} lines\n"
    )

    if not SIBLING.exists():
        print(f"{SIBLING} not present, skipping the adopting-codebase measurement")
        return

    print("\nShipGate, which existed before this library did:\n")
    sibling_lines = 0
    sibling_statements = 0
    width = max(len(site) for site in SIBLING_SITES)
    for site, description in SIBLING_SITES.items():
        counted, statements = adoption_lines(SIBLING / site)
        sibling_lines += len(counted)
        sibling_statements += statements
        print(
            f"{site:{width}}  {statements:>2} statements, "
            f"{len(counted):>2} lines   {description}"
        )

    sites = len(SIBLING_SITES)
    print(
        f"\n{'total':{width}}  {sibling_statements:>2} statements, "
        f"{sibling_lines:>2} lines across {sites} sites"
    )
    print(
        f"{'per site':{width}}  {sibling_statements / sites:>4.1f} statements, "
        f"{sibling_lines / sites:>4.1f} lines"
    )
    print(
        "\nreplaced: shipgate/tracing.py, 78 lines of its own tracing setup, deleted"
    )


if __name__ == "__main__":
    main()
