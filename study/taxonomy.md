# Pre-registered taxonomy and coverage hypothesis

Written before a single session was collected. The point of pre-registration is
that a taxonomy invented after looking at the data always fits the data, and a
coverage number chosen after seeing the result is not a prediction. The hash of
this file is committed before collection begins, and the commit order is visible
in `git log`.

Nothing below may be edited after collection starts. If it turns out to be wrong,
that is a result and gets written up as one.

## Part A: failure classes

What a session is labelled as, by hand, without seeing what the detectors said.
One label per session, first match wins, so the counts sum to the sample.

**A1. Clean.** Produced a verdict, no error, within the normal cost and latency
range for its runner.

**A2. Provider error, surfaced.** A call failed and the failure reached the
result. The item is scored zero and the error is recorded. This is the system
working: a broken judge cannot pass itself off as a model regression.

**A3. Provider error, absorbed by retry.** One or more attempts failed and a
later one succeeded. Invisible in the result, visible in the trace as a session
whose latency carries a wait it did not need to spend on work.

**A4. Cost outlier.** Completed normally but cost materially more than its peers
for the same runner. The threshold is set from the corpus, not guessed, and the
rule is stated in the analysis before it is applied.

**A5. Latency outlier.** Completed normally but took materially longer, with the
cold-start sessions excluded because a spun-down free tier is a known artefact
rather than a finding.

**A6. Malformed reply.** The provider answered, the answer did not parse, and the
item scored zero. Distinguished from A2 because nothing failed at the transport
layer and every status check passed.

**A7. Silently wrong.** A verdict was produced and recorded, and it is wrong on
inspection. Expected to be rare and expensive to label, and it is the class the
detectors have no signal for at all.

## Part B: the coverage hypothesis

Stated as a prediction, before the corpus exists, so it can be wrong.

A detector can only fire on a workload that emits the spans it reasons about.
Predicted coverage, by workload shape:

| Detector | Batch scorer (ShipGate) | Tool-using agent | Retrieval pipeline |
|---|---|---|---|
| `cost_ceiling` | fires | fires | fires |
| `loop` | cannot fire | fires | may fire |
| `silent_tool_failure` | cannot fire | fires | may fire |

**Prediction 1.** In the ShipGate corpus, `loop` and `silent_tool_failure` will
report exactly zero, and that zero will be a property of the workload rather than
evidence of health. Already observed twice at M5.7 on a small run; the corpus
tests whether it holds at 500 sessions.

**Prediction 2.** `cost_ceiling` will fire on a share of sessions determined
entirely by where the ceiling is set, so its precision against hand labels is a
statement about the threshold, not about the detector. The analysis must report
the ceiling alongside the rate or the number means nothing.

**Prediction 3.** Class A3, retry-absorbed errors, will be more common than A2,
surfaced errors. If so, the trace is the only place they are visible at all,
since the result carries no trace of a retried failure. This is the strongest
argument the study can make for tracing over logging, and it is written down here
before the data is seen so it cannot be retrofitted.

**Prediction 4.** No detector will fire on any A7 session. Nothing in the span
shape distinguishes a wrong verdict from a right one.

## What would falsify this

Prediction 1 fails if either detector fires even once on the corpus. That would
mean the workload emits tool spans I do not know about, and the coverage table is
wrong.

Prediction 3 fails if A3 is rarer than A2, which would weaken the tracing
argument and should be reported as such rather than dropped.

## Method notes fixed in advance

- Sample of 100 drawn uniformly at random from the collected sessions, seeded and
  the seed recorded.
- Labelling happens with the detector verdict hidden.
- Cold-start sessions are excluded from latency analysis and kept for everything
  else.
- `SPANLIGHT_FINGERPRINT_SALT` is pinned for the collection so fingerprints are
  comparable across processes, and the value is recorded but not published.
- Every provider, model and price used is recorded with the corpus. Costs are
  the counterfactual `spanlight.cost_usd_equivalent`, since real spend is zero on
  free tiers, and the prices behind it are unverified list prices.
