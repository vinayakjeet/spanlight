# A detector that cannot fire on your workload is worse than no detector

I built three failure detectors for LLM agents, ran them against 500 real
sessions, and found that two of them were structurally incapable of firing. Not
mistuned. Incapable. The dashboard showed zero loops and zero silent tool
failures for the entire corpus, and both zeros were facts about the workload
rather than facts about its health.

That is the result I want to lead with, because it is the one nobody publishes.
Every observability tool ships with detectors. None of them tell you, before you
adopt, whether the detectors can see anything on your system. A panel of zeros
reads as health, and nobody investigates a green dashboard.

## What I set out to measure

Spanlight wraps an LLM app's model, tool, and retrieval calls in one span per
run, then reads the shape of that run for three failures an ordinary monitoring
stack cannot see: a tool that errored while the agent carried on regardless, the
same tool called with identical arguments over and over, and a run that crossed a
spend ceiling.

The plan was a field study. Collect real traffic, hand-label a sample, report
precision and recall per detector, publish the corpus. Standard shape.

Before collecting anything I wrote the taxonomy and the predictions down and
committed the hash, so the predictions could be wrong on the record rather than
adjusted quietly afterwards. That turned out to matter more than I expected, and
not for the reason I expected.

## The corpus

500 sessions from ShipGate, an LLM-as-judge CI gate I built before this, running
against Groq for 46 minutes. Real traffic, real provider, full attribute set,
fingerprint salt pinned so hashes compare across processes.

Then the first result arrived, and it was not about agents at all.

**`loop` fired 0 times out of 500. `silent_tool_failure` fired 0 times out of
500.** The 95% interval on both is 0.0% to 0.8%.

The reason is one number: **the entire corpus contains zero tool spans.**
ShipGate is a batch scorer. One session per item, one judge call inside it, no
tools and no retrieval. Two of the three detectors reason about tool behaviour,
so neither can fire, at any threshold. Lowering the loop threshold from three to
two changes nothing when the count is zero.

Every unit test for those detectors passes. They pass against tool spans the
tests construct themselves, and the real workload never emits one.

## The third detector fired on everything

`cost_ceiling` fired on 500 of 500 sessions. I had set the ceiling below the
median session cost on purpose, to guarantee positives to measure precision
against. What I got was a detector with no discriminating power at all.

The cheapest session in the entire corpus carries a detection. A detector that
fires on everything carries exactly as much information as one that fires on
nothing. Both are constants.

So a detection rate quoted without the threshold beside it is not a result. That
is why Spanlight ships no default cost ceiling: any number invented in a library
gets quoted later as one somebody measured.

## Reading single traces found what the aggregates hid

The tables all looked reasonable. Then I opened three individual traces, and two
of them found errors in the study's own numbers.

**The session span was timing a queue.** I reported session latency as a median
of 1184.6ms. The provider's actual median is 559.1ms. The difference is items
waiting their turn: the host takes its concurrency semaphore *inside* the session
span, so an item queued behind three others holds an open span for the whole
wait. At concurrency 1, the fourth item of a batch shows a median session of
2027.6ms around a median model call of 523.0ms. The number tracks position in the
batch and nothing else. The median session span in this corpus is 51.9% queueing.

Worse, the line I had offered as the last remaining way to spot a retry, sessions
slower than twice the median, counted 19. Recomputed on model spans it counts
zero. All 19 were items waiting.

**Retries are invisible where the instrumentation sits.** The client retries
inside `complete()`, and the model span wraps `complete()`, so three attempts and
one attempt produce the same span, differing only in duration. Not in the result,
which records a success. Not in the span count, which is one either way. Only in
latency, which cannot separate a retry from a slow provider.

Both findings are the same lesson from opposite sides. A span measures its own
extent, so where you start it is not a stylistic choice, it is the measurement.
Wrap the call and you cannot see what happens inside it. Wrap the queue and you
cannot tell waiting from working.

## The one that changed the study

179 of the 500 sessions recorded a `fail` verdict, a stable 35.8%. I assumed
judge noise and nearly wrote it up that way.

Every item in the corpus was labelled `billing`, and the target under test
returns the constant string `billing`, so expected and output are identical in
all 500 sessions. The judge was handed both. The rubric's own comment says this
reduces the task to a string comparison, and that an `==` operator would agree
perfectly.

Measured, the judge agrees with `==` on 64.2% of items.

Because the corpus redacts prompt text by design, the corpus alone could only
reach the tickets through input token counts. Reading a finding off that is not
measurement, so I re-judged the eight tickets with the prompt recorded, three
times each. Zero verdicts changed. The three tickets that fail every time are the
three whose true intent is not billing: an app crash, a dead export button, and a
failed login. The judge said so in its own words, every time.

**The judge was right 500 times out of 500. My dataset was mislabelled on three
tickets out of eight.** The gate scored 0.642 and would have compared that
against a baseline at a threshold of 0.02, which is a rule sensitive to two
points applied to a number carrying 35.8 points of my own labelling error.

Nothing reported a problem. The provider answered, the reply parsed, the verdict
was recorded, the run completed. Every span was green.

No detector fires on this, at any setting. The pre-registered predictions said so
before collection: nothing in the span shape distinguishes a wrong verdict from a
right one. A span records that a call happened, what it cost, and whether it
raised. Whether the answer was correct is not a property of the call, so no
amount of span design recovers it.

The sting is in where the explanation lived. The judge's reason field said
exactly what was wrong, in plain English, and it sat in the process the whole
time. It is not on a span and it cannot go on one: it is model-generated text
about a customer ticket, which is what the redaction rule exists to keep out of a
shared trace store. The field that explains the failure is the field a
safe-to-export trace cannot carry.

## What this did to the method

The plan was to hand-label 100 sessions blind. After the above, that plan was
broken: a labeller reading blinded traces honestly would call all 179 of those
sessions clean, because that is what the trace shows. Labelling would have
published what a trace can show as though it were what happened, which is the
exact error this study exists to warn about, committed by the study itself.

So the labels are derived by stated rule over all 500 sessions instead, and the
human pass is kept with a smaller job: measuring how far a person reading a
blinded trace lands from what actually happened.

That is a deviation from a pre-registration I wrote specifically to stop myself
deviating. What protects it is narrow and worth stating precisely. The rules were
written after the corpus existed, so they are not pre-registered. They were never
written against a detector's output, which is the property the pre-registration
was protecting. The thresholds are Tukey's rule computed from the corpus rather
than tuned until the counts looked right. And the label ordering was my choice
after seeing the data, and it moves a number, so both scripts print the overlap
it hides.

## What it cannot tell you

The workload shape governs everything above. One system, one provider, one
46-minute window, and one shape. The coverage result generalises to batch scorers
and nowhere else. Every session succeeded, so three failure classes have no
instances at all and no failure rate can be quoted from this data.

The 500 sessions also draw on only eight tickets, whose verdicts are
deterministic, so they are not 500 independent trials and any confidence interval
over them overstates what the design supports.

## What I would tell someone adopting this

Count your tool spans first. If there are none, two of these three detectors are
decorative on your system, and the panels they populate will read as health
forever. It is one query and nobody runs it.

Set the cost ceiling from your own traffic or leave it off.

Think about where you wrap before you think about what you name things. Half of
what I got wrong here was a span boundary, not a missing attribute.

And do not expect any of this to tell you whether the answer was right. That was
predicted before collection, it held, and the corpus contains 179 green sessions
that prove it.

## Reproducing it

The corpus, the scripts, and every table are in `study/`. `analyse.py` reads
nothing but `corpus.jsonl` and prints the tables. `threats.md` leads with the
workload-shape limitation rather than closing on it. The corpus carries no prompt
or completion text, and a test sweeps the published file for it before it ships.
