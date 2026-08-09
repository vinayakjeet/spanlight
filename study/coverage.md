# Which detectors can see anything on your system

The question to ask before adopting any of this. A detector that cannot fire on
your workload is worse than no detector, because a panel of zeros reads as
health, and nobody investigates a green dashboard.

Every "cannot fire" below is measured, not reasoned. The measurement is
`study/analyse.py` over a corpus of real ShipGate sessions, and it was found by
running the thing rather than by thinking about it: the unit tests for all three
detectors pass, against tool spans the tests construct themselves and the real
workload never emits.

## The table

| Detector | Batch scorer | Tool-using agent | Retrieval pipeline |
|---|---|---|---|
| `cost_ceiling` | fires | fires | fires |
| `loop` | **cannot fire** | fires | may fire |
| `silent_tool_failure` | **cannot fire** | fires | may fire |

**Batch scorer** measured directly: ShipGate, a CI gate that scores a dataset
with an LLM judge. One session per item, one model call inside it, zero tool
spans in the entire corpus.

**Tool-using agent** and **retrieval pipeline** are not measured here. They are
stated from what each detector reads, and they are predictions this study cannot
settle. Saying so is the point: the honest version of this table has two columns
of measurement and two of reasoning, and mixing them silently is how a coverage
claim becomes marketing.

## What each detector needs to be true

**`cost_ceiling`** needs model spans with token counts, and a provider present in
`spanlight/list_prices.yaml`. That is nearly every LLM workload, which is why it
is the only one that fires everywhere. Its rate is set entirely by where the
ceiling sits, so a rate quoted without the ceiling beside it means nothing.

**`loop`** needs at least three tool spans in one session carrying the same name
and the same argument fingerprint. A workload that calls one tool once per
session can never produce them. It is not a threshold problem: lowering the
threshold to two changes nothing when the count is zero.

**`silent_tool_failure`** needs a tool span ending ERROR, a model span after it,
and a session that does not end ERROR. No tool spans, no first condition, no
detection, at any setting.

## The trap this exists to name

Both zeros above are indistinguishable, from a dashboard, from a system that is
working perfectly. `spanlight_detections_total{type="loop"}` sitting at zero for
a month means one of two things, and the metric cannot tell you which:

- nothing looped, or
- nothing **could** loop, because the workload emits no tool spans at all

The second is the dangerous one, and it is the state ShipGate was in for this
entire corpus. Anyone adopting these detectors should check the denominator
before trusting the numerator: count the tool spans, and if there are none, two
of these three panels are decorative.

That check is cheap and nobody does it. It is one query.

## A second, narrower finding

**A retry is invisible where the instrumentation currently sits.** ShipGate
retries inside `ChatClient.complete()` and the judge wraps `complete()` in a
single model span, so three attempts and one attempt produce the same span,
differing only in duration.

So a retry-absorbed provider failure appears: not in the result, which records a
success; not in the span count, which is one either way; and only in latency,
which cannot separate a retry from a slow provider.

This is a statement about **where you wrap**, not about tracing. Instrument
inside the retry loop and every attempt is visible. Instrument around the call
that contains it, which is the natural and more readable place, and the retries
vanish. The chassis `llm/client.py` has the same shape, so this applies to every
project in this portfolio that adopts the library the obvious way.

It was found by measuring a prediction the obvious way and getting a number that
was structurally zero rather than empirically zero.
