# Decisions

Every nontrivial choice gets an entry here at the time it's made - not
reconstructed later from memory. Newest entries at the top.

## Format
```
## YYYY-MM-DD: <short title>
**Context:** what problem/question forced a decision.
**Decision:** what was chosen.
**Alternatives considered:** what else was on the table, and why it lost.
**Consequences:** what this makes easier/harder later.
```

## 2026-08-06: The context manager is the primary form, not the decorator

**Context:** SPEC listed `@spanlight.model(provider, model)` as the adoption
API, with the context manager as a secondary convenience. Building M0.4 broke
that assumption immediately. The demo agent reads its provider from a settings
value per request, and the chassis `ChatClient.complete` takes the provider as
an argument, so neither call site has anything to name at import time. The one
place a decorator fits is a function that always talks to one model, and this
repo does not contain one.

**Decision:** `spanlight.model_span(provider, model=None)` is the primary form
and the decorator is built on top of it. `model` is optional, because the
chassis client passes no model and lets the provider choose. Callers set
`gen_ai.response.model` on the yielded span once the provider has answered.

**Alternatives considered:** a single object serving as both decorator and
context manager, in the style of `contextlib.ContextDecorator`. Rejected because
such an object has to hold the live span on itself, which corrupts its own state
the first time two coroutines enter the same instance. That is precisely the
concurrency bug M2.1 exists to catch, and shipping it inside the library that
catches it would be indefensible. A generator context manager gets a fresh
generator per use and cannot have the bug.

Also considered: requiring callers to pass a model string always. Rejected
because QUOTAS.md already records that `gemini-flash-latest` is a moving alias,
so what the provider actually served matters more than what was requested, and
forcing a guess at request time would put a fabricated value in the trace.

**Consequences:** the three-line adoption claim now rests on the context
manager, which is one line at the call site rather than one line above the
function. The decorator survives for call sites that are genuinely static, and
M4.2 will show whether ShipGate has any. Two names to document instead of one.

## 2026-08-06: The chassis `otel_bootstrap` is deleted, not wrapped

**Context:** the chassis ships `app/otel_bootstrap.py`, which calls
`trace.set_tracer_provider`. So does `spanlight.init()`. ShipGate carries a
near-identical third copy in `shipgate/tracing.py`.

**Decision:** `otel_bootstrap.py` is deleted in this repo and `create_app` calls
`spanlight.init()` instead.

**Alternatives considered:** keeping both and having `setup_otel` delegate.
Rejected because OpenTelemetry ignores a second `set_tracer_provider` and only
logs about it, so whichever ran first would win silently and the loser would
export nothing while appearing configured. A library built to surface exactly
that class of silent failure cannot ship with an instance of it.

**Consequences:** the demo app loses the chassis's `FastAPIInstrumentor` call,
so there are no HTTP server spans until M2.2 adds propagation deliberately. That
is a real regression for the length of one milestone and is recorded here rather
than discovered later. Every fork after this one inherits `spanlight.init()`
instead of `otel_bootstrap`, which is the deduplication that justifies the
project.

## 2026-08-07: Cost is two attributes, spend and counterfactual

**Context:** every price in `llm/providers/quotas.yaml` is `0.0`, which is
correct: the free tiers this portfolio runs on charge nothing. So
`ChatResponse.cost_usd` is always exactly zero. Three later milestones depend on
cost being a real number. The M3.3 ceiling detector could never fire, M6's
cost-per-session dashboards would render zeros, and M7.5's cost-of-failure
analysis would be a table of zeros. A detector that passes its unit tests while
being mathematically incapable of firing is how ShipGate's gate failed, twice.

**Decision:** two attributes. `spanlight.cost_usd` records what was actually
spent, which stays `0.0` and stays true. `spanlight.cost_usd_equivalent` records
what the same tokens would have cost at published paid-tier list prices, held in
`spanlight/list_prices.yaml`. The ceiling detector and the study's cost analysis
run on the equivalent; the README reports both.

**Alternatives considered:** reporting tokens only and dropping cost entirely,
which is unimpeachable but loses a dimension the brief asks for and one of the
Depth Chapter's three headline measures. Putting paid prices into `quotas.yaml`
so a single `cost_usd` is nonzero, rejected because that file is chassis-shared,
so the change would propagate to ShipGate and every future fork, and because
`cost_usd` would then overstate real spend everywhere it appears. A single
attribute that silently means "hypothetical" is a number waiting to be quoted as
spend.

**Consequences:** two names to explain, and `list_prices.yaml` is a second
pricing file to keep current. Every entry in it currently reads
`last_verified: UNVERIFIED`, because the figures came from documentation rather
than an invoice, so the equivalent is an order-of-magnitude estimate and the
README and study have to say so. A provider absent from the file produces no
equivalent at all rather than a zero, since a zero is indistinguishable from a
genuinely free provider and would drag a study average toward nothing.

## 2026-08-07: Tool argument fingerprints are salted per process by default

**Context:** M3.2 loop detection needs to know two tool calls were identical
without the trace carrying what they contained, and the M7 corpus gets published.
Tool arguments are often low entropy: a search query, a scheme name, an id. An
unsalted SHA-256 of `{"query": "pm-kisan"}` is recoverable by anyone willing to
guess candidate inputs, which would make a published corpus leak the arguments
the fingerprint existed to hide.

**Decision:** `sha256(salt + canonical_json(args))`, truncated to 16 hex
characters, with the salt read from `SPANLIGHT_FINGERPRINT_SALT` and otherwise
generated per process.

**Alternatives considered:** no salt, which is simpler and makes fingerprints
comparable everywhere, rejected for the brute-force exposure above. A fixed salt
committed to the repo, which is the same as no salt once the repo is public.

**Consequences:** loop detection is unaffected, because it only ever compares
fingerprints inside one live session in one process. Cross-process comparison is
lost by default, which the M7 corpus does want, so the collection run in M7.2
must set `SPANLIGHT_FINGERPRINT_SALT` explicitly and the study has to record
that it did. That is a real footgun: forgetting it produces a corpus where the
same tool call looks unique in every process, and nothing would flag it.

## 2026-08-07: Detectors run inside the span helper, not in a SpanProcessor

**Context:** the obvious place to run a detector is `SpanProcessor.on_end`. It
is where OpenTelemetry expects post-hoc analysis, and it is what BACKLOG M3.1
asks for. It cannot work here. `on_end` is handed a `ReadableSpan`, which has no
`set_attribute` method at all, so SPEC's requirement that a detection lands on
the offending span is unreachable from there. It does not degrade quietly
either: it raises `AttributeError` and takes the span down with it.

That bug shipped. The unit test passed a `MagicMock` span, which accepts any
method call and returns another mock, so a detector that would crash on every
real span looked green. This is the third instance in this portfolio of the
ShipGate pattern, a test that cannot fail standing in for one that can.

**Decision:** `_spans._span` calls the detector registry from a `finally` block
immediately before the span closes. That is the last moment the span is still
writable and the first moment its status and attributes are final. Session state
is an `OrderedDict` capped by insertion order rather than an unbounded dict.

**Alternatives considered:** a `SpanExporter` decorator that rebuilds each
`ReadableSpan` with added attributes, which does work and keeps detection out of
the hot path, rejected because reconstructing spans to edit them is a lot of
machinery to avoid a `finally` block, and it would put detections in the export
pipeline where the in-process metric counters of M3.5 cannot see them. Also
considered: mutating the still-open session span instead of the offending one,
rejected because SPEC S4 through S6 all name the offending span.

**Consequences:** detection cost moves onto the caller's thread. That is now a
latency number the M5 overhead budget has to account for, and it makes a slow
detector everyone's problem. Detectors are therefore required to be pure
dictionary arithmetic. It also means detection only works for spans created
through Spanlight's own helpers, so a span from an OTel contrib
instrumentation is invisible to detectors. That is acceptable: every detector in
SPEC reasons about tool and model spans, which are exactly the ones this library
creates.

## 2026-08-07: Head sampling cannot implement the detection override

**Context:** SPEC S8 says a sampled-out session exports anyway if it contains a
detection. M2.4 was written and marked done as a head sampler plus a processor
that flagged detected sessions. It cannot work, and the reason is structural
rather than a bug: a head sampler decides at span start, a detection is only
known at span end. By the time a session is known to be interesting, its earlier
spans have already been dropped and cannot be recalled. A detected session would
export with holes in it, which is worse than not exporting it, because a trace
that is missing children looks like a completed run that did less work.

**Decision:** M2.4 is reopened. `sample_rate` defaults to 1.0 so nothing is
dropped silently in the meantime, and the docstring states plainly that lowering
it currently drops detected sessions with the rest.

**Alternatives considered:** buffering every unsampled session in memory until
it ends and flushing on detection, which is tail sampling wearing a head
sampler's name and is explicitly a non-goal (SPEC non-goal 9), and would put an
unbounded span buffer in a library that claims a memory bound. Deferring the
whole question to a Grafana-side policy, rejected because the free tier has no
tail sampling and the portfolio operates no collector (non-goal 1).

**Consequences:** at rate 1.0 the Grafana free-tier ingest limit in QUOTAS.md
becomes the binding constraint on how much traffic the M7 study can collect, and
that number needs measuring before collection starts rather than after. The
honest version of S8 is probably "sample whole sessions, and accept that
detections in dropped sessions are counted by a metric but have no trace", which
would keep the counter accurate and give up the trace. That rewrite is a SPEC
change, so it is not being made silently here.

## 2026-08-07: A session is a span, not just a context variable

**Context:** `session()` set a `ContextVar` and nothing else. Every model, tool,
and retrieval span was therefore a parentless root, and OpenTelemetry gives each
root its own trace. A three-step run arrived in Tempo as three unrelated traces
that happened to share `spanlight.session.id`. The M1 demo checkpoint was
reported as a four-span waterfall on one trace id; it was not, and that claim was
wrong when it was made.

Three things depended on the waterfall that does not exist. SPEC S8's promise
that an exported session keeps its children was satisfied trivially, because no
session had children. M6's dashboards would have had no tree to render. And the
silent-failure detector had nowhere to put its verdict, since the tool span it
would have marked was closed by the time the verdict was knowable.

**Decision:** `session()` opens a real span named `session`, and every step
nests under it. It still yields the id rather than the span, because callers want
something to hand a user for finding the run.

**Alternatives considered:** span links instead of parentage, which is the OTel
answer for relating spans across traces, rejected because links relate peers and
this is genuinely a containment hierarchy, and because Tempo renders a parent
tree far better than it renders links. Also considered: leaving the shape alone
and joining on the attribute at query time, which is what the code implicitly
assumed, rejected because it gives up the waterfall that is the point of the demo
and makes duration-of-run a query rather than a span.

**Consequences:** one extra span per session, so span volume against the Grafana
free tier rises by roughly a third at three steps per run, which the M7 collection
budget has to account for. Every test asserting a span count changed, which is
the change being visible rather than a cost. The custom sampler became
unnecessary, below. Callers who used `session()` purely for the id now pay for a
span they may not want; that is the right default, and an opt-out can be added
if a real caller ever needs it rather than pre-emptively.

## 2026-08-07: The session sampler is OpenTelemetry's, not ours

**Context:** M2.4 shipped a hand-written `SessionSampler` holding its own
per-session decision cache. It had three faults. Two of its three return paths
passed a list where the SDK wanted a mapping, so it raised `TypeError` on its
own happy path, which no test caught because every unit test patches
`get_tracer` and so never constructs a sampler at all. Its decision cache was an
unbounded dict. And its detection-override branch was dead code, since nothing
populated the set it consulted.

**Decision:** delete it. `ParentBased(root=TraceIdRatioBased(rate))` from the
SDK does the job exactly, now that a session is one trace: a trace-id ratio
decides once per session by construction, and `ParentBased` makes every step
inherit the root's verdict, which is precisely S8's "dropped whole, never
halved". Eighty-five lines of custom code become one expression.

**Alternatives considered:** fixing the custom sampler in place, rejected once
the session span made the built-in exactly equivalent. Keeping it for the
detection override, rejected because that override cannot work from a head
sampler at all, for the reason recorded above.

**Consequences:** S8's detection exemption is now unimplemented rather than
implemented-but-inert, and BACKLOG says so. The honest version is probably to
count detections in dropped sessions with a metric and give up their traces,
which is a SPEC change and is not being made quietly. The sampler now has tests
that build a genuinely sampling provider, which is what would have caught the
`TypeError`, and the suite is that much slower for running a thousand sessions.

## 2026-08-08: A hop inherits the session id, and the test has to prove it

**Context:** M2.2 needed a trace to survive an HTTP boundary. The mechanism is
uncontroversial: OpenTelemetry's default propagator already carries both
`traceparent` and `baggage`, verified rather than assumed, so no custom
propagator is needed. The real question was what a second service should call
the run it is now part of.

**Decision:** the session id travels in W3C baggage under the same
`spanlight.session.id` key used for the span attribute, and `session()` accepts
inbound `headers`: it joins the caller's trace and adopts the caller's session id
rather than minting a new one. An explicit id still wins, so a caller who knows
better can say so.

**Alternatives considered:** a custom `X-Spanlight-Session` header, rejected
because a service in the middle running plain OpenTelemetry forwards baggage
untouched and would drop a header of ours, and the break would surface as two
unrelated sessions rather than as an error. Also considered: a fresh id per hop
joined at query time by trace id, rejected because the study counts sessions, so
a two-service run would be scored as two short ones and a failure in the second
hop would look like a session that simply ended.

**Consequences:** the session id is now attacker-supplied on a public endpoint,
since anyone can send baggage. It is only ever a grouping key, never a lookup
into anything, so the exposure is corpus pollution rather than access; a public
deployment collecting a study should ignore inbound baggage. Malformed
`traceparent` and `baggage` values have tests proving they do not raise, because
instrumentation that throws on a bad header turns a header into an outage.

**What this cost to get right:** the first version of both propagation tests
passed against an implementation that never attached the extracted context at
all. They built the outbound headers *inside* the caller's session, so the callee
inherited the ambient context and the trace ids matched for a reason unrelated to
propagation. Mutation testing caught it: deleting the `attach` call left every
test green. The tests now close the caller's session before the second hop, which
is what another process would look like, and that mutation fails them.

<!-- Add entries above this line. -->
