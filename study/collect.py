"""Collect the M7 corpus: real ShipGate sessions against real providers.

    cd d:/projects/shipgate
    set -a && . ./.env && set +a
    SPANLIGHT_FINGERPRINT_SALT=... python d:/projects/spanlight/study/collect.py 500

Writes one JSON object per span to `study/corpus.jsonl`, appending, so a run that
dies to a rate limit or a closed laptop resumes instead of starting over.

Spans go to the local file *and* to Grafana when an endpoint is configured. The
file is what the analysis reads: a free-tier Tempo has retention limits and no
bulk export, so a corpus that exists only there is a corpus that cannot be
re-analysed, by me or by anyone checking the study.

Paced deliberately below the free-tier limit. QUOTAS.md records a real Gemini 429
asking for 40 seconds while five attempts of backoff total about 31, so a run
that hammers the limit spends its night retrying inside a cooldown.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import random
import sys
import time

sys.path.insert(0, r"d:/projects/shipgate")

from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor  # noqa: E402

import spanlight  # noqa: E402

CORPUS = pathlib.Path(__file__).parent / "corpus.jsonl"
MANIFEST = pathlib.Path(__file__).parent / "corpus_manifest.json"

# Below Groq's free-tier ceiling on purpose. Four judge calls per gate run, so
# this paces to roughly 12 model calls a minute.
SECONDS_BETWEEN_RUNS = 20.0
ITEMS_PER_RUN = 4

# A low ceiling so cost_ceiling has positives to measure precision against. The
# analysis has to report this number beside the rate, or the rate is a statement
# about the threshold rather than about the workload.
CEILING_USD = 0.00005


class JsonlSink(SpanProcessor):
    """Every span, flattened, one per line.

    Deliberately not an OTLP exporter pointed at a local collector: that needs a
    collector running, and this has to survive an unattended overnight run on a
    laptop with nothing else installed.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self._file = path.open("a", encoding="utf-8")

    def on_end(self, span: ReadableSpan) -> None:
        context = span.get_span_context()
        self._file.write(
            json.dumps(
                {
                    "trace_id": format(context.trace_id, "032x"),
                    "span_id": format(context.span_id, "016x"),
                    "parent_span_id": (
                        format(span.parent.span_id, "016x") if span.parent else None
                    ),
                    "name": span.name,
                    "start_ns": span.start_time,
                    "end_ns": span.end_time,
                    "duration_ms": (span.end_time - span.start_time) / 1e6,
                    "status": span.status.status_code.name,
                    "attributes": dict(span.attributes or {}),
                    "events": [
                        {"name": e.name, "attributes": dict(e.attributes or {})}
                        for e in span.events
                    ],
                },
                default=str,
            )
            + "\n"
        )
        self._file.flush()

    def shutdown(self) -> None:
        self._file.close()


def sessions_so_far() -> int:
    if not CORPUS.exists():
        return 0
    with CORPUS.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if '"name": "shipgate.item"' in line)


def main() -> None:
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 500

    salt = os.environ.get("SPANLIGHT_FINGERPRINT_SALT")
    if not salt:
        raise SystemExit(
            "SPANLIGHT_FINGERPRINT_SALT must be set, or fingerprints are per process "
            "and the same tool call looks unique in every run. DECISIONS 2026-08-07."
        )

    assert spanlight.init(
        "shipgate-study",
        endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        headers=os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"),
        cost_ceiling_usd=CEILING_USD,
    ) or True  # a missing endpoint is fine here; the file is the corpus

    trace.get_tracer_provider().add_span_processor(JsonlSink(CORPUS))

    from shipgate.runners.base import execute_run
    from shipgate.runners.judge import JudgeRunner
    from shipgate.targets import StubTarget
    from shipgate.types import DatasetItem

    from llm import ChatClient

    prompts = [
        "I was charged twice this month",
        "the app crashes on launch",
        "refund my last invoice",
        "cannot log in after the update",
        "my card was declined for no reason",
        "the export button does nothing",
        "I want to cancel my subscription",
        "the totals do not add up",
    ]

    # Never the .env default. ShipGate ships LLM_PROVIDER=mock for its test
    # suite, and a corpus collected against a mock is a corpus of my own
    # scripted replies: twelve sessions were collected that way before this
    # check existed, and thrown out.
    provider = os.environ.get("STUDY_PROVIDER", "groq")
    if provider in {"mock", "scripted", "stub"}:
        raise SystemExit(f"refusing to collect a corpus against provider {provider!r}")
    client = ChatClient(max_retry_attempts=2)
    started = time.time()
    done = sessions_so_far()
    print(f"resuming at {done} sessions, target {target}, provider {provider}")

    while done < target:
        # Varied inputs across runs, so the corpus is not one prompt repeated and
        # the cost distribution has something to be a distribution of.
        chosen = random.sample(prompts, ITEMS_PER_RUN)
        items = [
            DatasetItem(
                id=f"s{done + n}",
                input={"prompt": text},
                expected="billing",
                slices=["batch"],
            )
            for n, text in enumerate(chosen)
        ]

        try:
            with spanlight.session(name="shipgate.gate"):
                asyncio.run(
                    execute_run(
                        JudgeRunner(client=client, provider=provider),
                        items,
                        StubTarget("billing"),
                        dataset_hash=f"study:{done}",
                    )
                )
        except Exception as exc:  # noqa: BLE001 - an overnight run must not die
            print(f"  run failed, continuing: {type(exc).__name__}")

        done = sessions_so_far()
        elapsed = time.time() - started
        rate = done / elapsed * 3600 if elapsed else 0
        print(f"  {done}/{target} sessions, {rate:.0f}/hour", flush=True)

        if done < target:
            time.sleep(SECONDS_BETWEEN_RUNS)

    trace.get_tracer_provider().force_flush(timeout_millis=30_000)
    MANIFEST.write_text(
        json.dumps(
            {
                "sessions": done,
                "provider": provider,
                "ceiling_usd": CEILING_USD,
                "salt_set": True,
                "collected_finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "taxonomy_sha256": (
                    pathlib.Path(__file__).parent / "taxonomy.sha256"
                ).read_text(encoding="utf-8").split()[0],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"done: {done} sessions in {(time.time() - started) / 60:.1f} minutes")


if __name__ == "__main__":
    main()
