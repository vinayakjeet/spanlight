# Three traces

Picked for contradicting something I believed when I started collecting, not for
illustrating a point already settled. Every id below is real and every attribute
is quoted from `study/corpus.jsonl`, so each one can be looked up rather than
taken on trust.

Two of the three found an error in this study's own numbers. That is the reason
to read individual traces at all: an aggregate cannot tell you it was computed
over the wrong interval, because it averages the mistake in along with everything
else.

---

## Trace 1: the detection that carries no information

**Trace** `cc6fd2b488552c11cb6abbdc2681d279`, session
`835d6e8758b441abad16f14f239ea6a8`, item `s57`.

```
shipgate.gate                             2835.2ms  UNSET
  shipgate.run    dataset_hash=study:56   2830.8ms  UNSET  score=0.75
    shipgate.item s57                     1203.5ms  UNSET  score=1.0
      chat                                 852.8ms  UNSET  verdict=pass
        gen_ai.usage.input_tokens   99
        gen_ai.usage.output_tokens  17
        spanlight.cost_usd_equivalent      0.00007184
        spanlight.detection                cost_ceiling
        event spanlight.detection
          cost.usd_equivalent              0.00007184
          cost.ceiling_usd                 0.00005
```

**What I expected.** A session carrying `spanlight.detection` is one worth
opening. That is the entire premise of putting the attribute on the span.

**What is actually there.** `s57` is the cheapest session in the corpus. Nothing
in 500 sessions cost less, and it is flagged. So are the other 499, because the
ceiling was set at $0.00005 and the median session costs $0.00007993. The
detector has no discriminating power at this setting; it is an expensive way to
count sessions.

I set that ceiling deliberately, to give `cost_ceiling` positives to measure
precision against, and it worked in the sense that there were positives. It also
produced a detector that fires on everything, which carries exactly as much
information as one that fires on nothing. Both are constants.

**What kept this diagnosable.** The event records `cost.ceiling_usd` beside
`cost.usd_equivalent`. One span is therefore enough to see that the detection is
noise: the value is $0.00007184 and the line it crossed is $0.00005, and no
reader needs to know how the emitting process was configured. Had the ceiling
lived only in that process's environment, this span would say a limit was
exceeded and give no way to find out which limit, six months after the container
that knew is gone. That is the argument for putting the threshold in the
detection rather than in the config, and it is worth more than the detection.

**For anyone adopting this.** A detection rate quoted without the threshold
beside it is not a result. `spanlight/_setup.py` refuses to ship a default
ceiling for the same reason: an invented number gets quoted later as a measured
one.

---

## Trace 2: the session span that is mostly a queue

**Trace** `6f127c6afbd71e4e7d022b77a9c02991`, items `s8` through `s11`, one gate
run at `concurrency=1`.

| item | session span | model span inside it | share outside the call |
|---|---|---|---|
| s8 | 663.9ms | 663.6ms | 0% |
| s9 | 1042.5ms | 561.0ms | 46% |
| s10 | 1525.7ms | 482.8ms | 68% |
| s11 | 2023.8ms | 497.5ms | 75% |

**What I expected.** A session span times what the session did. The study's
latency table was built on exactly that assumption.

**What is actually there.** It times what the session did plus how long it waited
to be allowed to start. `execute_run` opens the session span, then takes the
concurrency semaphore inside it, so an item that waits its turn holds an open
span while it waits. The model call is flat across the batch: 664, 561, 483, 498.
The session span climbs: 664, 1043, 1526, 2024. The rising number is position in
the batch, and nothing else.

Across the whole corpus, by batch position:

| position | sessions | median session span | median model span |
|---|---|---|---|
| 1 | 125 | 560.8ms | 560.2ms |
| 2 | 125 | 958.1ms | 574.3ms |
| 3 | 125 | 1522.8ms | 548.2ms |
| 4 | 125 | 2027.6ms | 523.0ms |

The median session spends 51.9% of its span outside the model call, and one
spends 82.5%.

**What this broke.** The study reported session latency as median 1184.6ms, p90
2134.4ms. Both numbers are real durations and neither is attributable to a
provider. The provider number is median 559.1ms, p90 713.7ms, and it is flat.
Worse, the "sessions slower than 2x median" line, offered as the last remaining
way to spot a retry, counted 19 sessions. Recomputed on model spans it counts
zero. All 19 were items waiting their turn.

The uncontaminated number was in the corpus the entire time. `shipgate.latency_ms`
is measured by the runner inside the semaphore and tracks the model span to
within 1.2ms across all 500 sessions. The analysis read `duration_ms` off the
session span instead, because that is the obvious field and it was named the
obvious thing.

`study/analyse.py` now prints both rows and the queue share. The row I got wrong
stays in the table rather than being replaced, since the gap between the two is
the finding.

**What it means.** This is the retry result from the other side. There, wrapping
the call meant the retries inside it were invisible. Here, wrapping the queue
means the wait is counted as work. A span measures its own extent and nothing
else, so where it starts is not a stylistic choice, it is the measurement. The
fix is not a new attribute: it is a span or an event on the semaphore wait, after
which the session span can keep meaning end to end and the split is visible.

---

## Trace 3: every detector silent, the verdict wrong, and the model the only thing that noticed

**Trace** `d71d4cc30fbcab95f195e4edaff681b1`, session
`820189e1621b45c2982706165ad6e473`, item `s480`, first in its batch and so free
of the queueing above.

```
shipgate.item s480                        555.7ms  UNSET  score=0.0
  chat                                    555.3ms  UNSET  verdict=fail
    gen_ai.usage.input_tokens   100
    gen_ai.usage.output_tokens  31
    spanlight.cost_usd_equivalent         0.00008349
    error.type                            (absent)
```

**What the trace says.** A completely ordinary session. Status is not ERROR at
any level. No `error.type`. Cost $0.00008349 against a median of $0.00007993.
Latency 555.7ms against a median model call of 559.1ms. It sits in the middle of
every distribution the study measured.

**What actually happened.** The judge was asked whether the output was good, and
it was handed both the expected intent, `billing`, and the model output,
`billing`. Those are the same string, in this session and in all 500, because
`StubTarget` returns a constant and `study/collect.py` labelled every item
`billing`. ShipGate's own note on rubric v1 says that handing the judge the
answer reduces the task to a string comparison, and that an `==` operator would
score kappa 1.0 on it.

Measured against llama-3.3-70b on Groq, that is false. `==` scores 1.0 on all
500. The judge agrees with it on 321, which is 64.2%.

**The 179 disagreements are not noise.** The corpus carries no prompt text, by
design, so the corpus alone can only reach the tickets through input token
counts, which partition 500 sessions into four groups of 1, 2, 4 and 1 tickets.
Reading a study off a reconstruction like that is not measuring anything, so
`study/replay_verdicts.py` judged the same eight tickets again with the prompt
recorded, three times each:

| ticket | input tokens | verdicts |
|---|---|---|
| I was charged twice this month | 101 | 3 pass |
| the app crashes on launch | 100 | 3 fail |
| refund my last invoice | 99 | 3 pass |
| cannot log in after the update | 101 | 3 fail |
| my card was declined for no reason | 102 | 3 pass |
| the export button does nothing | 100 | 3 fail |
| I want to cancel my subscription | 101 | 3 pass |
| the totals do not add up | 101 | 3 pass |

Zero tickets changed verdict across repeats, and the per-ticket token counts
match the corpus partition exactly: the 100-token group is 0 pass and 119 fail,
and it holds precisely the two tickets that fail every time.

The three that fail are the three whose true intent is not billing, and the judge
says so in the `reason` field it was already returning, recorded in
`study/replay_verdicts.json`:

> the app crashing on launch is unrelated to billing, expected intent should be
> related to technical issue

> The user is reporting an issue with logging in after an update, which is more
> related to a technical or account issue

> the issue described is related to a technical problem with the export button,
> not billing

A crash is technical, an export button that does nothing is technical, and being
unable to log in is account. The judge was right 500 times out of 500. The
dataset label was wrong on three tickets out of eight, and the judge objected
every single time it saw one.

The reason is not thrown away either. `JudgeRunner.score_item` keeps it on
`ItemResult.meta`, so it was in the process the whole time. It is simply not on a
span, which is why no amount of reading this corpus could have recovered it, and
that placement is not an oversight to fix. The reason is model-generated free
text about a customer ticket, which is exactly what SPEC non-goal 4 keeps off an
exported span by default, opt-in per call site and off in every project here. So
the one field that explained the failure is the one the redaction rule exists to
withhold. Worth stating plainly rather than discovering later: the price of a
trace that is safe to export is that it cannot carry the thing that would have
told you.

**What the system did with that.** Recorded it as `shipgate.score=0.0`, averaged
it into a run score of 0.75, and averaged 125 of those into 0.642, a number that
reads like a statement about the target's quality and is in fact a measurement of
my labelling error. That 0.642 is the number ShipGate's gate compares against a
baseline at a default threshold of 0.02, so a rule sensitive to two points is
being applied to a score with 35.8 points of label error in it. Nothing anywhere
reported a problem, because from every component's point of view nothing went
wrong: the provider answered, the reply parsed, the verdict was recorded, the run
completed.

**What the detectors saw.** At the collection ceiling this session carries a
`cost_ceiling` detection, along with all 500, which is trace 1's problem and not
a signal. At the shipped default, where Spanlight declines to invent a ceiling,
only `loop` and `silent_tool_failure` are registered and neither can fire on a
workload with no tool spans. So on the default configuration, no detector in this
library can see this session, at any threshold setting.

Prediction 4 in `study/taxonomy.md` said exactly that, before collection: nothing
in the span shape distinguishes a wrong verdict from a right one. It holds. Worth
being precise about why, since it bounds what this project can claim: a span
records that a call happened, what it cost, and whether it raised. Whether the
answer was correct is not a property of the call, so no amount of span design
recovers it. What would catch this is a rubric that does not hand the judge the
answer, which is what ShipGate's v2 and v3 exist for, plus agreement against
human labels. Neither is a tracing feature.

This is the class A7 session the taxonomy called rare and expensive to label. It
turned out to be 35.8% of the corpus, and the reason it was cheap to find is that
I had introduced it myself.

---

## What the three have in common

None of them is a missing field. Every attribute the SPEC contract promises was
present and correct in all three.

- Trace 1 is a threshold reported without the context that gives it meaning.
- Trace 2 is a span boundary drawn around a queue, so the measurement answered a
  question nobody asked.
- Trace 3 is a fact that lives outside the span entirely, on a call that
  succeeded in every sense the trace can observe.

The instrumentation was working perfectly in all three cases. What it was
measuring is the part that needed checking, and the only way I found any of it
was by opening single traces after the aggregates looked fine.
