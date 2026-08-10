"""Judge the same eight tickets again, one at a time, with the prompt recorded.

    cd d:/projects/shipgate
    set -a && . ./.env && set +a
    python d:/projects/spanlight/study/replay_verdicts.py 3

Not part of the corpus and it must never be merged into one. This runs after
collection to settle a question the corpus cannot answer about itself: which
ticket produced which verdict. `study/corpus.jsonl` carries no prompt text by
design, so the corpus can only be read through input token counts, and a study
that rests on inferring the input from its length has not measured anything.

Every judgement here is the same call the corpus made: rubric v1, expected
intent `billing`, model output `billing`, the string the stub target returns for
every item. Repeating each ticket also answers whether the verdict is stable,
which the corpus cannot show either, since it never judged the same ticket twice
under a recorded label.

Writes `study/replay_verdicts.json`.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import time
from collections import Counter

sys.path.insert(0, r"d:/projects/shipgate")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from study.collect import PROMPTS  # noqa: E402

OUT = pathlib.Path(__file__).parent / "replay_verdicts.json"

# What the corpus judged: a constant label against a constant target output.
EXPECTED = "billing"
STUB_OUTPUT = "billing"

SECONDS_BETWEEN_CALLS = 2.0


async def judge_once(client, provider: str, rubric, ticket: str) -> dict:
    from llm import ChatMessage

    messages = [
        ChatMessage(role="system", content=rubric.system),
        ChatMessage(
            role="user",
            content=rubric.render(ticket=ticket, expected=EXPECTED, output=STUB_OUTPUT),
        ),
    ]
    response = await client.complete(provider, messages)

    from shipgate.runners.judge import parse_verdict

    score, reason = parse_verdict(response.text)
    return {
        "verdict": "pass" if score == 1.0 else "fail",
        "reason": reason,
        "input_tokens": response.tokens_in,
        "output_tokens": response.tokens_out,
        "model": response.model,
    }


def main() -> None:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    provider = os.environ.get("STUDY_PROVIDER", "groq")
    if provider in {"mock", "scripted", "stub"}:
        raise SystemExit(f"refusing to replay against provider {provider!r}")

    from shipgate.runners.rubrics import RUBRIC_V1

    from llm import ChatClient

    client = ChatClient(max_retry_attempts=2)
    results: list[dict] = []

    for ticket in PROMPTS:
        for attempt in range(repeats):
            row = asyncio.run(judge_once(client, provider, RUBRIC_V1, ticket))
            row["ticket"] = ticket
            row["attempt"] = attempt
            results.append(row)
            print(
                f"  {row['verdict']:4}  tin={row['input_tokens']}  {ticket!r}",
                flush=True,
            )
            time.sleep(SECONDS_BETWEEN_CALLS)

    by_ticket: dict[str, dict] = {}
    for ticket in PROMPTS:
        rows = [r for r in results if r["ticket"] == ticket]
        verdicts = Counter(r["verdict"] for r in rows)
        tokens = {r["input_tokens"] for r in rows}
        by_ticket[ticket] = {
            "verdicts": dict(verdicts),
            "stable": len(verdicts) == 1,
            "input_tokens": sorted(tokens),
            "reasons": [r["reason"] for r in rows],
        }

    OUT.write_text(
        json.dumps(
            {
                "provider": provider,
                "model": results[0]["model"],
                "rubric": "v1",
                "expected": EXPECTED,
                "stub_output": STUB_OUTPUT,
                "repeats": repeats,
                "ran": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "by_ticket": by_ticket,
                "calls": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n{'verdicts':22}  {'tokens':8}  ticket")
    for ticket, summary in by_ticket.items():
        counts = ", ".join(f"{v} {k}" for k, v in sorted(summary["verdicts"].items()))
        print(f"{counts:22}  {str(summary['input_tokens']):8}  {ticket!r}")

    unstable = [t for t, s in by_ticket.items() if not s["stable"]]
    print(f"\ntickets whose verdict changed across {repeats} runs: {len(unstable)}")
    for ticket in unstable:
        print(f"  {ticket!r}")


if __name__ == "__main__":
    main()
