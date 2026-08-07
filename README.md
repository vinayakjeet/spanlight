# Spanlight

OpenTelemetry instrumentation for LLM agents. Session traces, token and cost
attribution, and three detectors for the failures a healthcheck reports as
healthy.

```python
import spanlight

spanlight.init(service="my-agent")

with spanlight.session(), spanlight.model_span(provider="groq"):
    ...
```

## Problem

An agent can return a 200 with a confident, fluent answer while its tool call
failed, it asked the same question nine times, and it burned ten times the
tokens it needed. Every signal an ordinary monitoring stack collects says that
run was fine, because by every measure it has, it was. CPU normal. No exception.
Latency unremarkable. The healthcheck is green because the healthcheck is
measuring the web server, and the web server did its job perfectly.

The failure is one layer up, in the shape of the run: which steps happened, in
what order, with what result. That shape is not in a metric or a log line. It is
in the trace, and only if the trace was built to hold it.

Spanlight builds it. A run becomes one span with its model, tool, and retrieval
calls beneath it, carrying tokens, cost, and the class of any error. Three
detectors then read that shape and mark the runs that were wrong in ways nothing
else would have reported.

The detectors observe. They never cancel a run or cap spend.

## Architecture

```
your agent
    |
    | spanlight.session()          one span, everything below it is a child
    |     spanlight.model_span()   gen_ai.* attributes, tokens, cost
    |     spanlight.tool_span()    name + salted argument fingerprint
    |     spanlight.retrieval_span()
    |
    v
detector registry                  runs while the span is still writable
    |                              loop / cost ceiling / silent tool failure
    v
OTLP over HTTP  ->  Grafana Cloud (Tempo traces, Prometheus counters)
```

A session is a real span, not a correlation id. That distinction is the whole
architecture: without an enclosing span every step is a parentless root, and a
three-step run arrives as three unrelated traces that happen to share an
attribute. There is no waterfall to read, and a session-scoped detector has
nothing still open to mark.

Detectors run from inside the span helper, immediately before the span closes,
rather than from a `SpanProcessor`. A processor's `on_end` receives a
`ReadableSpan`, which has no `set_attribute` at all, so a detection cannot be
written to the span that caused it from there.

### What lands on a span

| Attribute | Example |
|---|---|
| `gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.model` | `groq`, `llama-3.3-70b-versatile` |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | `412`, `88` |
| `spanlight.cost_usd` | `0.0` |
| `spanlight.cost_usd_equivalent` | `0.00031260` |
| `spanlight.session.id` | `b8774f33518a4993a10ba6ad12a913ef` |
| `spanlight.tool.name`, `spanlight.tool.args_fingerprint` | `lookup_scheme`, `9ac1f0b2c4d8e6a1` |
| `error.type` | `RateLimitError` |
| `spanlight.detection` | `silent_tool_failure` |

The full contract is in `SPEC.md`, and a test parses that table and fails in both
directions if the code and the documentation disagree. A dashboard written
against an attribute the code no longer emits returns nothing and looks fine
doing it, which is the same class of failure this project exists to catch.

### Cost is two numbers

`spanlight.cost_usd` is what was spent. On a free tier that is `0.0`, and it
stays `0.0`, because it is true.

`spanlight.cost_usd_equivalent` is what the same tokens would cost at published
list prices. One attribute serving both purposes fails in both directions: report
only real spend and the cost-ceiling detector can never fire, since the ceiling
sits above a number that is always zero. Report only the counterfactual and a
hypothetical figure ends up in a README where a reader takes it as spend.

The list prices in `spanlight/list_prices.yaml` are read from provider
documentation, not from an invoice. Every entry says `UNVERIFIED`, so the
equivalent is an order-of-magnitude estimate.

### The three detectors

**Loop.** The same tool with the same arguments, three times in one session.
Identity is a salted SHA-256 fingerprint, so a loop is reportable without the
trace carrying what was searched for. Arguments differing by one character
fingerprint differently and are counted separately, which keeps the detector off
an agent that is making progress.

**Cost ceiling.** Cumulative equivalent spend crosses a configured line. Fires
once, on the step that crossed it, not on every step after. Re-reporting would
turn one problem into a count of how many steps happened to follow it.

**Silent tool failure.** A tool span ended ERROR, a model span ran after it, and
the session did not end ERROR. That is a run told a tool broke which answered
anyway. It is only decidable when the run ends, so it lands on the session span.

Each fires three ways: an attribute for grouping, an event carrying why, and
`spanlight_detections_total{type,service}` for alerting. Alerting on the presence
of a span means querying traces on a timer, which a free tier will not carry.

## Benchmarks

Regenerate with `PYTHONPATH=. uv run python bench/overhead.py`.

CPython 3.13.2, Windows 11, AMD64. 20,000 calls per repeat, best of 7. Median
microseconds per call, in-process only.

| | disabled | enabled |
|---|---|---|
| `model_span` | 13.4us | 51.9us |
| `tool_span` | | 60.7us |
| `session` | 18.9us | 59.8us |
| `tool_span` with all three detectors | | 60.9us |

Two things worth reading off that table.

The disabled path costs 13.4us against SPEC S2's 50us budget, so instrumented
code left in place with no endpoint configured is close to free.

**The detectors are free.** 60.9us against 60.7us without them is inside the
noise between runs. They are dictionary arithmetic on the caller's thread, which
is why they are required to stay that way.

Export time is deliberately excluded. A `BatchSpanProcessor` hands spans to a
background thread and the caller never waits, so including it would measure
Grafana's latency and call it Spanlight's overhead.

Not yet measured: overhead under real concurrency, and memory under sustained
load. Those are M5. The field study numbers are M7 and do not exist yet.

## Technical decisions

`DECISIONS.md` has the full set with the alternatives that lost and what each
choice cost. The ones that shaped the design most:

**A session is a span.** It began as a `ContextVar`, which was wrong, and the
section above explains why.

**Detectors run in the span helper, not a `SpanProcessor`.** Forced by
`ReadableSpan` having no way to write to it. The cost is that detection happens
on the caller's thread, which is why the benchmark above measures it.

**Head sampling is OpenTelemetry's, not ours.** Because a session is one trace,
`ParentBased(TraceIdRatioBased(rate))` samples whole sessions and makes every
step inherit the root's verdict. That is exactly the guarantee wanted, so 85
lines of custom sampler were deleted.

**Fingerprints are salted per process.** Tool arguments are low entropy, so an
unsalted digest in a published corpus is recoverable by guessing.
`SPANLIGHT_FINGERPRINT_SALT` makes them comparable across processes when a study
needs that.

**Session ids travel in W3C baggage.** A service in the middle running plain
OpenTelemetry forwards baggage untouched but would drop a custom header, and that
break would surface as two unrelated sessions rather than as an error.

## What broke

**A detector could not mark the span that caused it.** The framework ran from
`SpanProcessor.on_end`, which hands out a `ReadableSpan` with no `set_attribute`.
It did not degrade quietly, it raised `AttributeError`. The unit test passed a
`MagicMock`, which accepts any method call and returns another mock, so a
detector incapable of surviving one real span reported green.

**A session was three traces.** `session()` set a context variable and nothing
else, so every step was a parentless root. A demo trace had been reported as a
four-span waterfall; it was one span, and the other three were elsewhere under
their own trace ids.

**The sampler raised `TypeError` on its own happy path.** Two of three return
paths passed a list where the SDK wanted a mapping. No unit test caught it,
because every test patches the tracer and so never constructs a sampler. It
surfaced against real Grafana.

**Propagation tests passed against code that ignored the headers.** They built
the outbound headers inside the caller's session, so the callee inherited the
context ambiently and the trace ids matched for an unrelated reason. Deleting the
`attach` call entirely left every test green.

The thread through all four is that a test which cannot fail is worse than no
test, because it is evidence. The ones that matter here are now checked by
mutation: break the rule deliberately, confirm the test goes red, put it back.

## Runbook

```bash
uv sync
uv run pytest -q
uv run ruff check .
```

Run the demo agent:

```bash
uv run uvicorn app.main:app --reload
curl -X POST localhost:8000/agent/run -H 'content-type: application/json' \
     -d '{"prompt":"hello"}'
```

The reply carries the `session_id` so a trace can be found rather than taken on
faith. A prompt containing `fail-tool` makes the demo tool raise, the route
swallows it and answers 200 anyway, and the silent-failure detector marks the
session. That is the whole argument in one request.

### Exporting

Tracing stays off until an endpoint is configured, and `init()` returns `False`
so a deliberate no-op is distinguishable from a silent failure.

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-<region>.grafana.net/otlp
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20<base64 token>
```

Give the signal-agnostic base URL. Spanlight appends `/v1/traces` and
`/v1/metrics` itself. Header values are percent-decoded, which Grafana's
`Basic%20` form requires and which a naive parser gets wrong in a way that looks
like a bad credential rather than a bad parser.

### Cold start

The demo is on Render's free tier, which spins down when idle. The first request
after a quiet period pays a cold start of roughly a minute and can look broken.
It is instrumented rather than hidden: the first span after a spin-up carries
`spanlight.cold_start`, so the dashboards annotate the outlier instead of
pretending it did not happen.
