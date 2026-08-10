# Adopting Spanlight

The recipe the other projects copy. Read the last section before you trust a
dashboard built on this: two of the three detectors cannot fire on some
workloads, and the panel of zeros they produce is indistinguishable from health.

## Install

```
uv add git+https://github.com/vinayakjeet/spanlight
```

Not on PyPI. A git dependency is the whole distribution story and CI installs
from that URL on every push, so a file missing from the wheel fails the build
rather than the first adopter.

## The three lines

```python
import spanlight

spanlight.init("your-service")            # once, at startup

with spanlight.session() as session_id:   # once per logical run
    with spanlight.model_span(provider="groq"):
        response = await client.complete(...)
        spanlight.record_usage(
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_usd=response.cost_usd,
            provider=response.provider,
        )
```

`init` returns `False` and imports nothing from the OTel SDK when no endpoint is
configured, so a service that has not set the environment variables pays for an
import and a function call. Instrumented code can stay in place unconditionally.

`session()` is the unit everything else is grouped by: one logical run, not one
process and not one HTTP request. It yields an id you can hand back to a user,
and it is a real span, so a run arrives in Tempo as one waterfall rather than as
a scatter of parentless spans that share an attribute.

Pass `name=` if your host has a better word for its unit of work. A hundred spans
all called `session` is a waterfall nobody can read.

Tools and retrieval, when you have them:

```python
with spanlight.tool_span("search", args={"q": query}):   # args are hashed, never recorded
    ...
with spanlight.retrieval_span("bm25", k=10):
    ...
```

## What it actually costs

Measured by `bench/adoption_diff.py`, counting every statement that mentions
`spanlight` plus its import, comments excluded.

| Repo | Sites | Statements | Lines | Per site |
|---|---|---|---|---|
| This repo | 3 | 11 | 20 | 3.7 statements, 6.7 lines |
| ShipGate | 4 | 19 | 29 | 4.8 statements, 7.2 lines |

ShipGate is the honest number, because it existed before this library did and had
its own working tracing. Adopting deleted `shipgate/tracing.py`, 78 lines of
setup, so the change was net negative in lines and the "three lines" claim is
true per call site only if you count the statement and not what it wraps. Where
it breaks down: a runner that needs the resolved model name back on the span
costs five statements, not three.

## Environment

| Variable | |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Grafana Cloud's signal-agnostic base URL, ending in `/otlp`. Spanlight appends `/v1/traces` and `/v1/metrics` itself. |
| `OTEL_EXPORTER_OTLP_HEADERS` | `Authorization=Basic%20<token>`, percent-encoded as Grafana issues it. Spanlight decodes it. |
| `SPANLIGHT_FINGERPRINT_SALT` | Optional. Fixes the tool-argument hash across processes. Leave it unset unless you are comparing fingerprints between runs, and never publish it. |

Both endpoint and headers can be passed to `init()` instead, which is what a host
reading config through pydantic-settings will want.

The two failure modes worth knowing, because both were inherited bugs here and
neither is visible from inside the process: an endpoint that already names a
signal gets nothing appended, and a header value that is not percent-decoded
arrives as a literal `Basic%20...` and returns 401, which reads like a bad
credential and sends you to regenerate a token that was never the problem.

## Per project

| Project | Instruments | When |
|---|---|---|
| ShipGate | Judge and pairwise runner LLM calls, gate CLI run as a session | Done |
| Chassis `llm/client.py` | Every `ChatClient.complete` call | Done |
| Tollgate | Inherits from the chassis client, plus route decisions and cache hits | Tollgate's M0 |
| Dastavez | Retrieval spans (BM25, dense, rerank), decomposition steps | Dastavez |
| Vaani | Per-stage latency spans, the source of truth for its waterfalls | Vaani |
| Dwarpal | Gateway tool calls as Spanlight-compatible events | Dwarpal |
| Turbine, Nishana, Chakravyuh, Phoenix, Copilot | Model and tool spans | Their own builds |

Anything forking the chassis inherits the instrumented `llm/client.py` and gets
model spans without doing anything. What it does not get is sessions, because
only the host knows what its unit of work is.

## Read this before trusting the dashboards

**Check whether the detectors can fire on your workload at all.** Two of the
three reason about tool spans. A workload that emits none, a batch scorer for
instance, reports zero loops and zero silent tool failures forever, and that zero
is indistinguishable on a dashboard from a system that is working. It is not a
threshold problem: lowering the loop threshold to two changes nothing when the
count is zero.

The check is one query. Count your tool spans. If there are none, two of the
three panels are decorative. `study/coverage.md` has the table.

**Set the cost ceiling from your own traffic or leave it off.** There is no
default, on purpose. In the field corpus a ceiling set below the median session
cost fired on 100% of sessions, which carries exactly as much information as
firing on none.

**Wrap deliberately, because a span measures its own extent.** Two findings from
the field study, both about placement rather than about tracing:

- Wrap a call that retries internally and the retries are invisible. Three
  attempts and one attempt produce the same span, differing only in duration.
- Open a session span before taking a concurrency semaphore and the wait is
  counted as work. In the corpus the median session span was 51.9% queueing, and
  the study published that as latency before anyone noticed.

**Sampling below 1.0 costs you detections too.** A dropped session cannot carry
the attribute or the event, so it is not counted in
`spanlight_detections_total` either, and an alert on that counter sees the same
fraction as Tempo does.

**Nothing here evaluates whether the answer was right.** A span records that a
call happened, what it cost, and whether it raised. In the field corpus, 35.8% of
sessions produced a wrong verdict and every one of them was green at every level.
No detector fires on that, at any setting, because correctness is not a property
of the call that produced it.
