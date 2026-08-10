# Proof Artifact: a silent tool failure, and the green report beside it

**Induced, and labelled induced.** SPEC A6 prefers a genuine failure and permits
this fallback. It is being taken because the field corpus cannot supply the
genuine article: 500 real ShipGate sessions produced zero tool failures, since a
batch scorer emits no tool spans at all. See `study/coverage.md`.

What is induced is the tool erroring. What is not induced is anything after it.
The agent swallows the error the way agent code swallows tool errors, the route
returns a normal answer, and the detector notices without being told to.

Trace `087e93cce514c07988defa4d6dd09f6f`, session `b050c18cadd44faba42e1964284546a7`.
Prompt: `fail-tool: which subsidy applies to a marginal farmer`

Exported to the configured endpoint, so this id resolves in Tempo.

## 1. The trace

```
session                      +    0.0ms      6.8ms  UNSET  DETECTION=silent_tool_failure
  tool lookup_scheme           +    4.9ms      0.1ms  ERROR  error.type=SchemeIndexUnavailable
  retrieve demo-index          +    5.1ms      0.0ms  UNSET
  chat                         +    5.4ms      1.1ms  UNSET
    attempt 1                    +    5.6ms      0.1ms  UNSET
```

The failing span is `tool lookup_scheme`, carrying `error.type=SchemeIndexUnavailable`. Two
spans later the run calls a model anyway, and the session ends without an error
status. That combination is the rule: a tool ended ERROR, a model span started
after it in the same session, and the session ended OK. An agent was told a tool
failed and carried on as though it had not.

The detection lands on `session`, with the event:

```json
{
  "spanlight.detection.type": "silent_tool_failure",
  "spanlight.detection.tool.name": "lookup_scheme"
}
```

## 2. The alert

Not captured here. The rule is checked in as `dashboards/alert_detections.json`
and fires on `increase(spanlight_detections_total)`, but firing it needs a
Grafana org and this artifact is generated on a laptop with none. Outstanding.

## 3. What everything else reported

This is the piece that makes the artifact land. The failure being present is
unremarkable. The rest of the stack calling it a success is the argument.

The caller got **HTTP 200** and a normal reply:

```json
{
  "text": "mock reply: fail-tool: which subsidy applies to a marginal farmer",
  "provider": "mock",
  "model": "mock-echo",
  "tokens_in": 8,
  "tokens_out": 10,
  "cost_usd": 0.0,
  "latency_ms": 0.31609999132342637,
  "session_id": "b050c18cadd44faba42e1964284546a7"
}
```

The host's own structured logs for the same request:

```
{"service": "spanlight-demo-agent", "endpoint": "<otlp-endpoint>", "event": "spanlight.enabled", "level": "info", "timestamp": "2026-08-10T10:38:54.397635Z"}
{"provider": "mock", "model": "mock-echo", "tokens_in": 8, "tokens_out": 10, "cost_usd": 0.0, "latency_ms": 0.31609999132342637, "event": "llm.call", "request_id": "198ed631-98f8-4d15-8d75-aa1e15d88e93", "level": "info", "timestamp": "2026-08-10T10:38:54.454321Z"}
{"method": "POST", "path": "/agent/run", "status_code": 200, "latency_ms": 35.208800007239915, "event": "http.request", "request_id": "198ed631-98f8-4d15-8d75-aa1e15d88e93", "level": "info", "timestamp": "2026-08-10T10:38:54.456035Z"}
```

No error field, no non-zero exit, nothing for a healthcheck to go red on. An
uptime monitor watching this endpoint sees a 200. A log-based alert watching for
`level=error` sees nothing. The only thing in the entire run that noticed is the
span attribute and the counter that came from it.

## Regenerating this

```
uv run python eval/proof_artifact.py
```

It exits non-zero and writes nothing if no detection fires. Publishing a
waterfall with no detection on it, and describing it as one, would be the exact
failure this project exists to make visible.
