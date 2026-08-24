# Running a Collector in front of Spanlight

The library exports OTLP straight to Grafana Cloud and the portfolio operates no
collector of its own (SPEC non-goal 1). `collector/config.yaml` is for adopters
who run one anyway, and it exists because three jobs belong at a collector and
cannot be done honestly inside an instrumented process:

1. **Normalisation.** The contrib `genainormalizer` processor maps OpenInference
   and OpenLLMetry attributes onto the same `gen_ai.*` conventions Spanlight
   emits natively. Those two cover sixty-plus frameworks between them, so this
   is what makes a mixed fleet readable as one fleet: a LangChain service built
   on OpenLLMetry and a hand-instrumented service built on Spanlight produce
   spans a single dashboard can query.

2. **Redaction.** The library can only control what it emits, and its refusal to
   record prompt text is enforced by a canary test over its own output. A
   collector sees everything crossing the wire, including spans from
   instrumentation this project does not own. Its allowlist drops any key not
   named in it; its blocked patterns replace credential-shaped values even on
   allowed keys. The allowlist is held to `docs/CONTRACT.md` by
   `tests/test_collector_config.py`, so a renamed attribute fails CI rather than
   silently disappearing from production traces.

3. **Tail sampling.** Keep every session that ended in error or ran slow, sample
   the routine remainder at the stated rate. This is the override SPEC S8 asked
   for and a head sampler structurally cannot make: a head decision happens
   before an error exists, so the library declines to fake it and says so in
   DECISIONS.md instead. In the collector it is three policies, with the rates
   written into the file next to them.

## What is deliberately not in the default allowlist

The content capture attributes (`gen_ai.input.messages` and siblings) are
emitted by the library only when an operator enables them, and they are excluded
from the collector allowlist even then. Exporting prompt bodies therefore
requires changing something in two places: the environment variable where the
library runs, and the allowlist where the collector does. One stray variable on
one service should not be able to turn a redacting pipeline into a prompt
pipeline without anyone noticing.

## Running it

Needs an `otelcol-contrib` build recent enough to ship `genainormalizer`, or a
custom build including that component. Early builds spell the selection field
`profiles`; if your build rejects `sources` in the config, that is the rename.

```
GRAFANA_OTLP_ENDPOINT=otlp-gateway-<region>.grafana.net/otlp
GRAFANA_AUTHORIZATION="Basic <base64 token>"
otelcol-contrib --config collector/config.yaml
```

Point services at the collector's port 4318 instead of at Grafana Cloud. Metrics
bypass normalisation, redaction and tail sampling: counters and histograms are
already low-cardinality by construction, and holding them back behind a
ten-second sampling window would buy nothing and delay alerts.

## Not verified here

No Docker on this machine (SPEC A8), so this pipeline has never run end to end.
What CI does hold is the static contract: processor order, both dialects named,
every emitted attribute allowed except the content trio, error and slow
policies at 100%, a stated routine rate, and no credential anywhere in the file.
The runtime behaviour is the part you verify on first deploy.
