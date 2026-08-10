"""Produce the Proof Artifact: a silent tool failure, and the green report beside it.

    PYTHONPATH=. uv run python eval/proof_artifact.py

Writes `docs/proof/`. Three pieces are called for and this script produces two of
them, because the third is a screenshot of a Grafana alert and this machine has
no org to fire it in. What it does produce is checked in as text rather than as
images, so a reader can diff it and re-run it.

**The failure here is induced, and it is labelled induced everywhere it appears.**
SPEC A6 prefers a genuine one and allows this fallback. The corpus is the reason
it is being taken: 500 real ShipGate sessions against Groq produced zero provider
errors, zero malformed replies and zero tool failures, because a batch scorer
emits no tool spans at all. Waiting for a genuine silent tool failure on that
workload is waiting for something the workload cannot do.

What is not induced is the part the artifact is actually about. Nothing here
patches the detector or hands it a constructed span. The demo agent swallows a
tool error the way real agent code swallows tool errors, the request returns 200,
and the detector notices without being told to.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

import spanlight._spans as spans_module  # noqa: E402
from spanlight._detector_framework import SESSION, registry  # noqa: E402
from spanlight._detectors import (  # noqa: E402
    loop_detector,
    silent_tool_failure_detector,
    watch_for_silent_failure,
)
from spanlight.attributes import DETECTION, ERROR_TYPE, SESSION_ID  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "proof"

PROMPT = "fail-tool: which subsidy applies to a marginal farmer"


def waterfall(spans: list) -> str:
    """The trace as a reader sees it in Tempo, in the order things started."""
    ordered = sorted(spans, key=lambda s: s.start_time)
    if not ordered:
        return "(no spans)"
    origin = ordered[0].start_time
    depth = {}
    lines = []
    for span in ordered:
        parent = span.parent.span_id if span.parent else None
        depth[span.context.span_id] = depth.get(parent, -1) + 1
        offset = (span.start_time - origin) / 1e6
        duration = (span.end_time - span.start_time) / 1e6
        marks = []
        if span.attributes.get(ERROR_TYPE):
            marks.append(f"error.type={span.attributes[ERROR_TYPE]}")
        if span.attributes.get(DETECTION):
            marks.append(f"DETECTION={span.attributes[DETECTION]}")
        lines.append(
            f"{'  ' * depth[span.context.span_id]}{span.name:<28} "
            f"+{offset:7.1f}ms  {duration:7.1f}ms  {span.status.status_code.name:<6} "
            f"{'  '.join(marks)}".rstrip()
        )
    return "\n".join(lines)


def run() -> tuple[list, object, str, bool]:
    """One request through the real app, watched by an extra exporter.

    The in-memory exporter is added to whatever provider the app configured,
    rather than replacing it. Swapping the tracer would keep the spans local and
    print a trace id that is findable nowhere, and piece 1 of this artifact is a
    Tempo waterfall. So with an endpoint configured, this run genuinely exports:
    the id below can be pasted into Tempo. Unset `OTEL_EXPORTER_OTLP_ENDPOINT` to
    generate the artifact without sending anything.
    """
    from fastapi.testclient import TestClient
    from opentelemetry import trace

    from app.main import create_app

    logs = io.StringIO()
    with redirect_stdout(logs):
        app = create_app()

    exporter = InMemorySpanExporter()
    configured = trace.get_tracer_provider()
    exporting = hasattr(configured, "add_span_processor")
    if exporting:
        configured.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        local = TracerProvider()
        local.add_span_processor(SimpleSpanProcessor(exporter))
        spans_module.get_tracer = lambda: local.get_tracer("proof-artifact")

    # The default set, minus the cost ceiling, which has no default and would
    # need a number this artifact has no reason to invent.
    registry.clear_detectors()
    registry.register(loop_detector)
    registry.register(watch_for_silent_failure)
    registry.register(silent_tool_failure_detector, phase=SESSION)

    with redirect_stdout(logs), TestClient(app) as client:
        response = client.post("/agent/run", json={"prompt": PROMPT})

    if exporting:
        configured.force_flush(timeout_millis=15_000)

    return list(exporter.get_finished_spans()), response, logs.getvalue(), exporting


def main() -> None:
    spans, response, logs, exported = run()
    detected = [s for s in spans if s.attributes.get(DETECTION)]
    failed = [s for s in spans if s.attributes.get(ERROR_TYPE)]

    if not detected:
        raise SystemExit(
            "no detection fired, so there is no artifact to publish. Publishing "
            "the waterfall anyway would be the exact failure this project is about."
        )

    OUT.mkdir(parents=True, exist_ok=True)
    session_id = detected[0].attributes.get(SESSION_ID)
    trace_id = format(detected[0].context.trace_id, "032x")

    (OUT / "waterfall.txt").write_text(waterfall(spans) + "\n", encoding="utf-8")
    (OUT / "spans.json").write_text(
        json.dumps(
            [
                {
                    "name": s.name,
                    "status": s.status.status_code.name,
                    "duration_ms": (s.end_time - s.start_time) / 1e6,
                    "attributes": dict(s.attributes or {}),
                    "events": [
                        {"name": e.name, "attributes": dict(e.attributes or {})}
                        for e in s.events
                    ],
                }
                for s in sorted(spans, key=lambda s: s.start_time)
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    detection_event = next(
        (
            dict(event.attributes or {})
            for span in detected
            for event in span.events
            if event.name == "spanlight.detection"
        ),
        {},
    )

    (OUT / "artifact.md").write_text(
        ARTIFACT.format(
            trace_id=trace_id,
            session_id=session_id,
            prompt=PROMPT,
            waterfall=waterfall(spans),
            failing_span=failed[0].name if failed else "(none)",
            failing_type=failed[0].attributes.get(ERROR_TYPE) if failed else "(none)",
            detecting_span=detected[0].name,
            detection_event=json.dumps(detection_event, indent=2),
            findable=(
                "Exported to the configured endpoint, so this id resolves in Tempo."
                if exported
                else "Generated with no endpoint configured, so this id is local to "
                "the run that produced it and resolves nowhere."
            ),
            status_code=response.status_code,
            reply=json.dumps(response.json(), indent=2)[:600],
            logs=(logs.strip() or "(the host logged nothing at all)"),
        ),
        encoding="utf-8",
    )

    print(f"trace {trace_id}")
    print(waterfall(spans))
    print(f"\nHTTP {response.status_code}, {len(failed)} failed span, {len(detected)} detection")
    print(f"written to {OUT}")


ARTIFACT = """# Proof Artifact: a silent tool failure, and the green report beside it

**Induced, and labelled induced.** SPEC A6 prefers a genuine failure and permits
this fallback. It is being taken because the field corpus cannot supply the
genuine article: 500 real ShipGate sessions produced zero tool failures, since a
batch scorer emits no tool spans at all. See `study/coverage.md`.

What is induced is the tool erroring. What is not induced is anything after it.
The agent swallows the error the way agent code swallows tool errors, the route
returns a normal answer, and the detector notices without being told to.

Trace `{trace_id}`, session `{session_id}`.
Prompt: `{prompt}`

{findable}

## 1. The trace

```
{waterfall}
```

The failing span is `{failing_span}`, carrying `error.type={failing_type}`. Two
spans later the run calls a model anyway, and the session ends without an error
status. That combination is the rule: a tool ended ERROR, a model span started
after it in the same session, and the session ended OK. An agent was told a tool
failed and carried on as though it had not.

The detection lands on `{detecting_span}`, with the event:

```json
{detection_event}
```

## 2. The alert

Not captured here. The rule is checked in as `dashboards/alert_detections.json`
and fires on `increase(spanlight_detections_total)`, but firing it needs a
Grafana org and this artifact is generated on a laptop with none. Outstanding.

## 3. What everything else reported

This is the piece that makes the artifact land. The failure being present is
unremarkable. The rest of the stack calling it a success is the argument.

The caller got **HTTP {status_code}** and a normal reply:

```json
{reply}
```

The host's own structured logs for the same request:

```
{logs}
```

No error field, no non-zero exit, nothing for a healthcheck to go red on. An
uptime monitor watching this endpoint sees a 200. A log-based alert watching for
`level=error` sees nothing. The only thing in the entire run that noticed is the
span attribute and the counter that came from it.

## Regenerating this

```
PYTHONPATH=. uv run python eval/proof_artifact.py
```

It exits non-zero and writes nothing if no detection fires. Publishing a
waterfall with no detection on it, and describing it as one, would be the exact
failure this project exists to make visible.
"""


if __name__ == "__main__":
    main()
