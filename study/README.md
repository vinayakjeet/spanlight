# The corpus

500 real ShipGate sessions, collected against Groq in a single 46 minute window
on 2026-08-09, with the instrumentation running exactly as a host application
would run it. Everything the study claims is regenerable from the files in this
directory, and the point of publishing it is that you can disagree with the
analysis without having to take the corpus on trust.

Start with `taxonomy.md`, which was written and hashed before a single session
was collected, so the predictions in it can be wrong. `corpus_manifest.json`
records that hash, and `sha256sum taxonomy.md` should reproduce it.

## Regenerating the tables

```
PYTHONPATH=. uv run python study/analyse.py         # every table in the study
PYTHONPATH=. uv run python study/derive_labels.py   # a class per session, by rule
PYTHONPATH=. uv run python study/precision.py       # detector precision and recall
```

`analyse.py` reads nothing but `corpus.jsonl`. It prints the coverage result
first, because that is the finding the study is built around, and the cost and
latency tables after it.

`derive_labels.py` assigns each session a taxonomy class from rules stated in the
file, and writes `labels_derived.jsonl`. The plan was to hand-label a sample
instead; `threats.md` section 8 records why that changed and what the change
costs. It also reports which classes it cannot derive, rather than reporting them
as zero.

`precision.py` scores the detectors against those labels. If `labels.jsonl`
exists it also reports how far a human labelling blind lands from the derived
label, which is a different measurement and the one a human is actually needed
for.

## What a line is

One JSON object per span, in the order spans ended, which is not the order they
started.

| field | |
|---|---|
| `trace_id`, `span_id`, `parent_span_id` | hex, `parent_span_id` null at the root |
| `name` | `shipgate.gate`, `shipgate.run`, `shipgate.item`, or `chat` |
| `start_ns`, `end_ns` | monotonic nanoseconds from the collecting process |
| `duration_ms` | derived, and see the warning below |
| `status` | `UNSET`, `OK` or `ERROR` |
| `attributes` | the span attributes, verbatim |
| `events` | span events, each with its own attributes |

The tree is one gate run holding one `shipgate.run`, holding four
`shipgate.item` sessions, each holding one `chat` model call. A session is an
item, not a gate run, so `spanlight.session.id` on a `shipgate.item` span is the
key that groups a session together.

**`duration_ms` on a `shipgate.item` span is not latency.** The host takes its
concurrency semaphore inside the session span, so an item that waits its turn
holds an open span while it waits, and the median session span here is 51.9%
queue. Use the `chat` span's duration, or `shipgate.latency_ms`, which the host
measures inside the semaphore and which agrees with the `chat` span to within
1.2ms across all 500 sessions. `traces.md` has the full account, and the study
published the wrong one first.

## What is deliberately not here

- **No prompt or completion text.** No ticket, no judge reply, no reason string.
  `tests/study/test_corpus_is_publishable.py` sweeps the file for all eight
  tickets and holds every string attribute to an allowlist with a length bound,
  so an attribute added later that carries a reply or a stack trace fails the
  build rather than shipping.
- **No fingerprint salt.** It was pinned during collection so fingerprints
  compare across processes, and the manifest records only that it was set.
  Publishing it would let anyone rebuild the fingerprint of any tool call whose
  arguments they can guess, which is what hashing them was for.
- **No credentials, checked by prefix** against the shapes the free tiers issue.

`replay_verdicts.json` is the exception and is not part of the corpus. It records
24 calls made after collection, with the ticket and the judge's reason kept,
because the question it settles is which ticket produced which verdict and that
cannot be answered by a file with the tickets removed. The eight tickets in it
are synthetic and were written for this study. Do not merge it into the corpus.

## What this corpus cannot support

Stated here rather than at the end of the analysis, because these bound every
number above.

- **Every session succeeded.** Groq did not fail once, so taxonomy classes A2,
  A3 and A6 have no instances and nothing here measures how the workload fails
  at the provider. Class A7, a verdict produced and wrong, is the opposite case:
  179 sessions, 35.8%, every one of them green.
- **Two of the three detectors cannot fire on it at all.** Zero tool spans in
  500 sessions. Their zero is a fact about the workload, not evidence of health,
  and `coverage.md` is the table to read before adopting any of this.
- **The third fired on everything.** The ceiling was set below the median
  session cost on purpose, to produce positives. It produced a constant.
- **The dataset labels are wrong on three of eight tickets.** Every score in the
  corpus carries 35.8 points of label error, which the judge caught and the
  harness did not. Cost and latency are unaffected; anything derived from
  `shipgate.score` or `shipgate.verdict` is not. `traces.md`, trace 3.
- **One system, one provider, one workload shape, one window.** The last is the
  one that generalises worst.

## Collecting it again

```
cd <shipgate>
set -a && . ./.env && set +a
SPANLIGHT_FINGERPRINT_SALT=<any fixed value> python <spanlight>/study/collect.py 500
```

Appends, so a run interrupted by a rate limit or a closed laptop resumes. Paced
at roughly 12 model calls a minute, which is deliberate: a run that hammers a
free tier spends its night retrying inside a cooldown. Delete `corpus.jsonl`
first unless you mean to extend this one, and expect different numbers, since
the provider, the model, and the day are all part of the result.
