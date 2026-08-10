# Threats to validity

Up front, not at the end, because the first one governs whether any of the rest
is worth reading.

## 1. One workload shape, and it is the shape that determines the headline result

Every session here comes from a batch scorer: a CI gate that calls an LLM judge
once per dataset item. That shape emits no tool spans at all, which is why two of
the three detectors return zero, and it is the entire reason the coverage result
exists.

So the coverage table generalises to batch scorers and nowhere else. The columns
for tool-using agents and retrieval pipelines in `coverage.md` are reasoned from
what each detector reads, not measured, and they are labelled that way there. A
reader who runs an agent that calls tools in a loop should expect completely
different numbers, and this study cannot tell them what those numbers are.

Retiring this one takes a second system with a different shape, not a longer run
of this one. That is the first item on the depth ladder for a reason.

## 2. The dataset labels are wrong on three of eight tickets

Found at trace 3 and confirmed by replay. Every item was labelled `billing`,
three of the tickets are not billing, and the judge failed exactly those three
every time it saw them.

- **Unaffected:** cost, latency, token counts, span structure, detector coverage,
  and the retry and queueing findings. None of these read the score.
- **Affected:** everything derived from `shipgate.score` or `shipgate.verdict`.
  The corpus-wide score of 0.642 measures my labelling, not the target.
- **Bounded, not neutral:** the A7 rate of 35.8% is the rate of a defect I
  introduced. It says that a mislabelled dataset produces silently wrong verdicts
  that no span can distinguish from correct ones, which is the claim the study
  actually makes. It says nothing whatsoever about how often real datasets are
  mislabelled.

## 3. The sample is 500 sessions but eight tickets

The 500 sessions draw from eight tickets, four per gate run, and each ticket's
verdict is deterministic across all three replays. Sessions are therefore not
independent trials, and any confidence interval computed over 500 of them
overstates what the design supports for anything ticket-dependent.

The analysis prints intervals for detector firing rates, where the unit really is
the session, and deliberately refuses to print one for the A7 rate, where the
unit is the ticket and n is 8.

## 4. One provider, one model, one 46 minute window

Groq, `llama-3.3-70b-versatile`, 46 minutes on 2026-08-09. Zero failures in that
window, which is the reason three taxonomy classes have no instances and why no
failure rate can be quoted from this data at all. A different provider, a paid
tier, or a bad afternoon would produce a different corpus, and the honest reading
of "zero provider errors" is "none in 46 minutes on one provider".

## 5. The corpus records instrumentation choices as much as behaviour

Two findings here are about where spans start, not about what the system did.
Retries are invisible because the model span wraps a call that retries
internally, and half the median session span is queueing because the session span
opens before the concurrency semaphore. Both were found and both are written up,
but they are a warning about the whole corpus: a span boundary decides what a
number means, and other numbers here may rest on boundaries nobody has questioned
yet.

## 6. Cost is counterfactual

Real spend was zero. `spanlight.cost_usd_equivalent` applies unverified list
prices to real token counts, so it is a comparison quantity and not money.
Comparing two sessions in this corpus is sound. Quoting a dollar figure from it
is not.

## 7. No cold starts

Collection ran locally against a local host, so no session carries
`spanlight.cold_start` and the cold-start exclusion the taxonomy fixed in advance
never had anything to exclude. The Render free-tier spin-down latency artefact is
therefore unmeasured here, and the README's cold-start notice rests on separate
manual checks rather than on this corpus.

## 8. The labels are derived by rule, which is a deviation from the plan

The pre-registered method was a human labelling 100 sessions blind. Trace 3 broke
its premise: nothing in a span distinguishes a wrong verdict from a right one, so
a labeller reading blinded traces honestly would call all 179 A7 sessions A1, and
the study would have published what a trace can show as though it were what
happened.

`study/derive_labels.py` assigns a class to all 500 sessions by stated rule
instead. That is a real deviation and it carries real risk, so what protects it
is worth being exact about, along with what does not:

- The rules were written after the corpus existed, so they are not
  pre-registered. Only the taxonomy and the sampling method were.
- They were never written against a detector's output, which is the property the
  pre-registration existed to protect. A label chosen by looking at what fired
  scores a detector against itself.
- The outlier thresholds are Tukey's rule computed from the corpus, so they were
  not tuned until the counts looked right.
- The ordering was chosen by me, after seeing the data, and it moves a number.
  A7 takes precedence over A4, which is why A4 has no members despite four
  sessions clearing the cost fence. `derive_labels.py` prints that overlap and
  `precision.py` says so where the 0% appears, because a reader who sees only the
  distribution would conclude no session was expensive.
- Class A3 is not derivable at all, and is reported as unmeasured rather than as
  zero.

`labels.jsonl` still does not exist, so the human pass is unrun. It has a smaller
and better-defined job now: not to produce the ground truth, but to measure how
far a person reading a blinded trace lands from it. The A1-against-A7 cell of
that table is the size of the gap between what a trace shows and what happened,
and it is the number this whole study is about.

## 9. One author, and pre-registration only covers what was written down

The person who wrote the detectors wrote the collection script, the taxonomy, and
this analysis. `taxonomy.md` was hashed before collection and the hash is in the
manifest, which constrains the predictions and the sampling method, and nothing
else. Everything decided after collection, including which three traces to
annotate and how to read them, is unprotected by that and should be read as the
author's argument rather than as a pre-committed result.
