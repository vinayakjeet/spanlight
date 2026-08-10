# The emitted contract

Every attribute and metric Spanlight puts on the wire. This is the document a
dashboard, an alert, or a downstream consumer is written against, so it lives
here rather than in a planning file, and three test modules parse it and hold it
to `spanlight/attributes.py` in both directions.

That mattering is not hypothetical. These tables previously lived in a working
file that is deliberately untracked, so the tests parsing them passed on the
machine that had it and failed on every CI runner from the day they were written.
A contract test that cannot run where it counts is the shape of bug this whole
project exists to make visible, and it went unnoticed here for weeks.

Names follow the OpenTelemetry GenAI semantic conventions. The pinned convention
revision is recorded as a resource attribute, because a trace whose attribute
names came from a different revision than the dashboard querying it is worse than
no trace: it looks queryable and returns nothing.

## Span attributes

| Attribute | Type | Example | On |
|---|---|---|---|
| `gen_ai.system` | string | `gemini` | model |
| `gen_ai.operation.name` | string | `chat` | model |
| `gen_ai.request.model` | string | `gemini-flash-latest` | model |
| `gen_ai.response.model` | string | `gemini-2.5-flash-002` | model |
| `gen_ai.usage.input_tokens` | int | `412` | model |
| `gen_ai.usage.output_tokens` | int | `88` | model |
| `spanlight.cost_usd` | double | `0.0` | model |
| `spanlight.cost_usd_equivalent` | double | `0.00013` | model |
| `spanlight.session.id` | string | `run-2f9c` | all |
| `spanlight.tool.name` | string | `search_schemes` | tool |
| `spanlight.tool.args_fingerprint` | string | `9ac1f0b2c4d8e6a1` | tool |
| `spanlight.retrieval.index` | string | `schemes-v3` | retrieval |
| `spanlight.retrieval.k` | int | `8` | retrieval |
| `spanlight.attempt.number` | int | `2` | attempt |
| `spanlight.cold_start` | bool | `true` | first span after spin-up |
| `spanlight.semconv_version` | string | `1.29.0` | resource |
| `error.type` | string | `RateLimitError` | any |
| `spanlight.detection` | string | `silent_tool_failure` | offending span |

The offending span is the step that tripped the rule, for the loop, cost and
retry detectors. For a silent tool failure it is the session span, because that
rule is not decidable until the run ends, by which point the tool span has closed
and an ended span cannot be written to.

`error.type` is the exception class name and never the message. A message carries
user data and gives the attribute unbounded cardinality, and both were found
leaking by a redaction canary rather than by reading the code.

## The detection event

Alongside the attribute, a detection adds a `spanlight.detection` span event. The
attribute is what a dashboard groups by; the event is what a human reads once the
dashboard has pointed them at a trace. Splitting them keeps the attribute low
cardinality while letting the event carry the arithmetic the detector already
did, instead of leaving the reader to re-derive it.

| Event attribute | Type | Example | On |
|---|---|---|---|
| `spanlight.detection.type` | string | `loop` | every detection |
| `spanlight.detection.tool.name` | string | `search_schemes` | loop, silent_tool_failure |
| `spanlight.detection.tool.calls` | int | `3` | loop |
| `spanlight.detection.cost.usd_equivalent` | double | `0.00063` | cost_ceiling |
| `spanlight.detection.cost.ceiling_usd` | double | `0.00047` | cost_ceiling |
| `spanlight.detection.retry.failed_attempts` | int | `4` | retry_amplification |
| `spanlight.detection.retry.threshold` | int | `4` | retry_amplification |

Every detection carries the threshold it crossed next to the value that crossed
it. A breach is only meaningful beside the line it broke, and the line is a
deployment choice a reader six months later has no way to recover from the
process that made it.

## Metrics

| Metric | Type | Labels |
|---|---|---|
| `spanlight_detections_total` | counter | `type`, `service` |
| `spanlight_session_cost_usd` | histogram | `service` |
| `spanlight_export_failures_total` | counter | `service`, `reason` |
| `gen_ai_client_token_usage` | histogram | `service`, `gen_ai.system`, `type` |

The label sets are an allowlist, enforced by
`tests/spanlight/test_metrics_cardinality.py`, and the omission worth naming is
the session id. It is on every span, it reads like a useful grouping, and as a
metric label it creates one time series per run, which exhausts a free tier's
active-series limit within days. Session-level questions belong to the trace
store, which is built for exactly that cardinality.

Two notes for anyone querying these in Prometheus. Dots are not legal in label
names, so `gen_ai.system` arrives as `gen_ai_system`, and a query written the way
this table spells it returns nothing. And `spanlight_session_cost_usd` declares
no unit deliberately: the OTLP to Prometheus translation appends a unit when it
is not already a suffix, and this name ends in `_usd` already.
