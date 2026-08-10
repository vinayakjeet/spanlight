"""Send enough traffic at Grafana to tell a working dashboard from a broken one.

    uv run python eval/generate_traffic.py 120

Reads `.env` itself, and needs no `PYTHONPATH`. Both of those were once the
caller's job, expressed as a line of bash that fails silently on cmd.exe: `set -a`
reports "Environment variable -a not defined" and carries on, so the script then
ran with no endpoint configured and sent its traffic nowhere. A setup step that
can half-succeed is a setup step this script should be doing itself.

**Synthetic, and never to be confused with the field corpus.** `study/corpus.jsonl`
is 500 real sessions against a real provider and it is what the study reports on.
This makes up traffic in the shapes the panels query, so that an empty panel means
a broken query rather than a quiet system. Nothing here should ever be analysed.

It exists because verifying a dashboard against no data is impossible: every
panel renders empty, and empty is exactly what a wrong metric name, a flattened
label, and an idle service all look like. So this emits at least one of
everything the dashboards ask for, including all four detection types.

The part that matters and that a one-off script gets wrong: **it flushes both
providers.** Traces leave through a batch processor and metrics through a
periodic reader, and a process that exits promptly takes whatever has not been
sent with it. `eval/proof_artifact.py` flushes traces only, which is correct for
what it does and would leave every metric panel here empty.
"""

from __future__ import annotations

import os
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from opentelemetry import metrics, trace  # noqa: E402
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor  # noqa: E402

import spanlight  # noqa: E402
from spanlight.attributes import DETECTION  # noqa: E402


class Tally(SpanProcessor):
    """Counts detections on their way out, wherever they landed.

    Reading the session span alone undercounts: only `silent_tool_failure` marks
    the session, and the other three mark the step that tripped them. That is the
    design, and it is also why a script watching one span reported a healthy
    fleet on traffic built to be unhealthy.
    """

    def __init__(self) -> None:
        self.fired: dict[str, int] = {}

    def on_end(self, span: ReadableSpan) -> None:
        kind = (span.attributes or {}).get(DETECTION)
        if kind:
            self.fired[kind] = self.fired.get(kind, 0) + 1

ENV_FILE = pathlib.Path(__file__).resolve().parents[1] / ".env"


def load_env() -> None:
    """Read `.env` into the process, without overwriting what is already set.

    Deliberately not `python-dotenv`: this is eight lines and adding a dependency
    to a script that exists to verify dashboards is the wrong trade. Values are
    taken verbatim, including the percent-encoded Grafana auth header, which
    `spanlight.init` decodes.
    """
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


SERVICE = os.environ.get("TRAFFIC_SERVICE", "spanlight-demo-agent")

# Set from the shapes below rather than by eye, and this is the second time that
# distinction has bitten in this repo. At 0.00004 it fired on 40 sessions out of
# 40, which is what the field study found when the corpus ceiling sat under the
# median: a detector that fires on everything carries the same information as one
# that fires on nothing, and it swamps the panel it is meant to demonstrate.
#
# A healthy session here costs 0.00013 to 0.00074 equivalent, and `expensive`
# costs 0.00236. This sits above the healthy ceiling and below the outlier.
CEILING_USD = 0.001


class ToolBroke(Exception):
    pass


class Transient(Exception):
    pass


def healthy() -> None:
    with spanlight.tool_span("lookup_scheme", args={"id": random.randint(1, 9999)}):
        pass
    with spanlight.retrieval_span("schemes-v3", k=random.choice([3, 5, 8])):
        pass
    model_call()


def model_call(tokens_in: int | None = None, tokens_out: int | None = None) -> None:
    with spanlight.model_span(provider="groq"), spanlight.attempt_span(1):
        spanlight.record_usage(
            tokens_in=tokens_in or random.randint(180, 900),
            tokens_out=tokens_out or random.randint(30, 260),
            cost_usd=0.0,
            provider="groq",
        )


def silent_tool_failure() -> None:
    """A tool errors, the run answers anyway, the session ends clean."""
    try:
        with spanlight.tool_span("lookup_scheme", args={"id": 42}):
            raise ToolBroke("scheme index unavailable")
    except ToolBroke:
        pass
    model_call()


def loop() -> None:
    """The same tool, the same arguments, past the measured threshold."""
    for _ in range(4):
        with spanlight.tool_span("search", args={"q": "pm-kisan eligibility"}):
            pass
    model_call()


def expensive() -> None:
    """Enough tokens to clear the ceiling on its own."""
    model_call(tokens_in=2400, tokens_out=1200)


def retry_storm() -> None:
    """Three calls, each burning two attempts before it lands."""
    for _ in range(3):
        with spanlight.model_span(provider="groq"):
            for attempt in (1, 2, 3):
                if attempt < 3:
                    try:
                        with spanlight.attempt_span(attempt):
                            raise Transient("429")
                    except Transient:
                        pass
                else:
                    with spanlight.attempt_span(attempt):
                        spanlight.record_usage(
                            tokens_in=300, tokens_out=60, cost_usd=0.0, provider="groq"
                        )


# Weighted so most traffic is healthy. A fleet where every session trips
# something is not a fleet anyone would recognise, and the detections panel is
# only meaningful against a denominator.
SHAPES = (
    [healthy] * 12
    + [silent_tool_failure] * 3
    + [loop] * 2
    + [expensive] * 2
    + [retry_storm] * 1
)


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 120

    load_env()
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        raise SystemExit(
            f"OTEL_EXPORTER_OTLP_ENDPOINT is not set and {ENV_FILE} does not "
            "provide it, so this would generate traffic and send it nowhere."
        )
    print(f"exporting to {endpoint}")

    assert spanlight.init(SERVICE, cost_ceiling_usd=CEILING_USD), "init returned False"

    tally = Tally()
    trace.get_tracer_provider().add_span_processor(tally)

    random.seed()
    started = time.time()

    for n in range(count):
        shape = random.choice(SHAPES)
        with spanlight.session() as session_id:
            shape()
        if n == 0:
            print(f"first session: {session_id}")
        # Spread across the window so the timeseries panels have a shape rather
        # than one spike. A rate over a flat line is indistinguishable from no
        # data on a 24 hour view.
        time.sleep(0.25)

    print(f"\n{count} sessions in {time.time() - started:.0f}s, service {SERVICE}")
    for kind, n in sorted(tally.fired.items()):
        print(f"  {kind:22} {n:>3}")
    missing = {"loop", "cost_ceiling", "silent_tool_failure", "retry_amplification"} - set(
        tally.fired
    )
    if missing:
        print(f"  no detection of type: {', '.join(sorted(missing))}")
        print("  Raise the session count. A panel with one series in it cannot")
        print("  show you that grouping by type works.")

    # Both, and this is the whole reason the script exists. A batch processor and
    # a periodic reader each hold what they have not sent, and a process that
    # exits promptly takes it with it.
    print("\nflushing traces and metrics")
    trace.get_tracer_provider().force_flush(timeout_millis=30_000)
    metrics.get_meter_provider().force_flush(timeout_millis=30_000)
    print("done. Metrics land on the next scrape, so give the panels a minute.")


if __name__ == "__main__":
    main()
