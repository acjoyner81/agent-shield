# 0014 rationale: Land telemetry in Splunk reproducibly and visibly

Decision record for [0014](index.md). Read by humans and by `/architect` on update. `/develop`
does not need this file.

## Context

No telemetry has ever reached Splunk. This is not an outage that started recently; it is a
pipeline that has never once completed a delivery, and nothing in the system said so.

The evidence, gathered by probing the running stack:

- The aggregator posts to `http://splunk:8088/services/collector/raw`. Splunk's HTTP Event
  Collector answers only on a TLS port, because the shipped default config sets
  `enableSSL=1`. The same request that returns `Connection reset by peer` over plain HTTP
  returns `200 {"text":"Success","code":0}` over HTTPS, with the token header unchanged.
- The aggregator's failure log renders the exception as an empty string, so it has been
  logging `Shipment failed (attempt 1/5): .` with no reason, for as long as it has been
  running.
- The `splunk` service has no volumes. The one override that enables the collector, a
  `[http] disabled=0` line, was written three minutes after that container started and
  exists only in the container's writable layer. Nothing in the repository creates it, and
  recreating the container discards it.
- Splunk is absent from the probe list at `gateway/main.py:308`, so `/v1/health/services`
  never mentions it. The only signal was Docker's container healthcheck, which runs
  `/sbin/checkstate.sh` and tests that the `splunkd` process is alive. That is why Docker
  has been reporting Splunk healthy throughout (basis: a health check that asserts process
  liveness rather than the real dependency hides exactly this class of failure).
- `telemetry:dlq` holds 366 events and grows without bound. `gateway/aggregator.py:155`
  acknowledges the stream entries whether or not the ship succeeded, which is only safe
  because the dead letter copy exists, and nothing ever drains that list.
- Three separate places post to the collector, each with its own hardcoded URL: the
  aggregator on `/raw`, `observability/splunk_exporter.py` on `/event`, and
  `POST /v1/telemetry/logs` at `gateway/main.py:661` on `/services/collector`. The last of
  these also sends no `Authorization` header, never reads the token, uses a 0.5 second
  timeout, and swallows the error into a `print`. It cannot succeed under any
  configuration.
- `scrub_pii` is defined at `gateway/aggregator.py:35` and never called. The personal data
  masking that spec 0001 requires has never run on anything.

Two things in the repository state this plainly. The `splunk` service is not handed the
collector token at all; only `telemetry-aggregator` gets the Splunk environment. And
`.env` sets `SPLUNK_ENABLED=false`, which is read by zero lines of code, so the
configuration reads as though shipping is deliberately switched off while it is in fact
on and failing.

The forces shaping the decision: shipping has never worked, so there is no working
behaviour to preserve and no data at risk in the collector. The wider deployment pattern
is already established, since the Tripwire work took the same shape of making container
state reproducible from the repository rather than by hand. And the project treats
observability as a first class surface, with an admin dashboard that already reports four
services.

> Premise note: this topic spans more than one decision. Deployment reproducibility, the
> certificate trust, collapsing three shipping paths, the replay surface, the health
> surface, and a new permission scope are each implementable on their own. They are not
> independently valuable, though: replaying events is pointless while nothing ships, and a
> health entry for a path that cannot work would be a lie. This spec treats them as one
> decision with a fixed order, and the build plan splits it so that task 5 is the first
> point at which anything works end to end and is proven (basis: the project's Tracer
> Bullet approach in `docs/scope/scope.md`).

## Options considered

### Option 1: Fix in place, keep the aggregator

Correct the URL scheme, mount a certificate authority, add the volumes, retire the two
duplicate call sites, and add the missing operator surfaces.

**Pros**:
- Nothing has ever shipped, so there is no working path to protect and no traffic to
  migrate. The strangler pattern earns its cost when you have a live system to run
  alongside a new one; here there is nothing to run alongside.
- Smallest change that satisfies every acceptance criterion.
- Keeps the aggregator service that spec 0002 already designed and that already reads the
  stream correctly (basis: spec 0002's batching decision is sound and needs no change).

**Cons**:
- Carries forward the single threaded aggregator, which is a single point of failure by
  design.
- Touches infrastructure, three modules and a permission scope in one slice, so the diff is
  broad even though each part is small.

### Option 2: Replace with a strangler, new pipeline alongside

Build a second shipping path, run both, cut over, retire the old.

**Pros**:
- The old path stays available until the new one is proven.

**Cons**:
- There is no working old path. Running an aggregator that has never delivered a single
  event alongside a replacement buys no confidence, and doubles the surface for exactly
  this kind of configuration bug.
- Two shipping paths would mean the same transport fix has to be applied and verified twice,
  which is how the divergence happened in the first place (basis: strangler pattern is for
  live systems, not for a pipeline with no traffic).

### Option 3: Replace directly, rewrite the telemetry path

Discard the stream and aggregator, and ship from the gateway process directly to Splunk.

**Pros**:
- Removes a container and the Redis stream hop.
- One process, one code path, no distributed handoff to reason about.

**Cons**:
- Puts a network call on the gateway's request path, which is exactly what the stream was
  introduced to avoid (basis: spec 0001 and spec 0002 both chose buffering so that
  gateways are never blocked by the log platform).
- Discards a working, tested consumer group implementation for no gain in correctness.
- Loses batching, so throughput cost moves onto Splunk per event.

### Sub decision: how consumers trust the collector's certificate

**Mount Splunk's certificate authority and verify against it (chosen)**. The volumes are
being added anyway, so Splunk's own authority becomes mountable, and consumers verify
against it. Nothing to configure per environment, and the collector token stays encrypted
on the wire instead of travelling in an `Authorization` header in plaintext.

**Turn TLS off on the collector port**. Simplest, no certificate plumbing at all, and it
matches the code exactly as written. Rejected because the collector token is a credential
sent on every batch, and plaintext inside a container network is a weaker guarantee than
the certificate the stack already has.

**An environment variable naming a certificate authority bundle, verification on by
default**. Flexible across environments, and it can fail loudly at startup when the path is
missing. Rejected as more configuration surface than the mount needs, and it makes the
trust anchor something each consumer must be told rather than something they can read.

**A verification flag that permits turning it off**. Fewest moving parts. Rejected because a
flag whose purpose is to disable a protection will be set to false by someone in a hurry
and left there, and this project has already had a security control quietly fail open (the
`permissions` claim collision, where an advisory check called a broken control harmless).

## Rationale

Option 1 is chosen because the defining constraint is that this pipeline has never
delivered anything. The usual argument for the strangler pattern, that you cannot safely
swap a working path for an unproven one, does not apply when the working path is a
pipeline whose only observable behaviour is a blank error and a growing list. There is
nothing to migrate and no traffic to cut over, so the migration machinery would be pure
cost. The genuine risks here are configuration risks, not data risks, and configuration
risks are answered by making the deployment reproducible and proving one event lands,
which is what tasks 1 to 5 do.

The volume decision is a consequence rather than a preference. Persisting `/opt/splunk/etc`
is what makes the collector's authority available to consumers and what stops the hand
written override from disappearing on the next recreate. Persisting `/opt/splunk/var` is
what stops the same recreate from silently deleting every event the moment it starts
working. Given that, mounting the authority is nearly free, which is why it wins over
disabling TLS or adding a per consumer configuration variable.

Collapsing the three call sites is treated as part of this decision rather than as cleanup,
because the divergence is the mechanism that let the bug persist. One path to the collector
means the scheme and the certificate authority are configured once, and a regression is a
single test rather than three.

On the replay operation: the atomic script is chosen deliberately over the simpler pop and
requeue, because the project has already shipped that exact bug once. Claiming first and
writing second stranded a metered event so that its dead letter entry redrived as a
duplicate and the usage was dropped permanently. `gateway/metering.py` now uses one script
for claim and rollup for this reason, and reusing that pattern here costs one script and
removes a whole class of loss.

## Evidence

Probe results from the running stack, recorded so a future reader can recheck rather than
take this on trust.

| Check | Result |
|---|---|
| `http://splunk:8088/services/collector/raw` with token | `Connection reset by peer` |
| `https://splunk:8088/services/collector/raw` with token | `200 {"text":"Success","code":0}` |
| `https://splunk:8088/services/collector/event`, empty body | `400 {"text":"No data","code":5}` |
| HTTPS with certificate verification on | `CERTIFICATE_VERIFY_FAILED: self signed certificate in certificate chain` |
| `[httpEventCollector]` stanza anywhere under `/opt/splunk/etc` | none; this version configures the collector through the `[http]` stanza |
| `[http]` stanza, shipped default | `disabled=1`, `port=8088`, `enableSSL=1` |
| `[http]` stanza, local override | `disabled = 0`, written 3 minutes after container start, not in the repository |
| `volumes` on the `splunk` compose service | none |
| `LLEN telemetry:dlq` | 366 |
| `XLEN telemetry:queue` | 367, one consumer group `telemetry-aggregator`, 1 pending |
| `scrub_pii` call sites outside its own definition | none |

## References

**Project sources** (verifiable in this repository):
- `AGENTS.md`: the operational notes on rebuilding `gateway-python` before trusting port
  8000, and on driving Redis from inside the container because the host Redis is a
  different instance.
- spec 0001, telemetry standard: the personal data masking requirement that `scrub_pii`
  was written to satisfy and never wired up.
- spec 0002, Redis to Splunk aggregator: the batching and dead letter design this spec
  keeps, and whose AC-1 was ratified as met without ever having been verified.
- spec 0008, event schema: the payload shape carried by the stream.
- spec 0012, public API: AC-5 confirms telemetry ingest is deliberately outside the curated
  contract, which is what allows its response to change here.
- `gateway/metering.py`: the atomic Lua script for claim and rollup, the precedent for the
  replay script.

**Practices & standards**:
- Strangler pattern, for migrating a live path onto a replacement without a cutover risk.
- Health checks that assert the real dependency rather than process liveness.
- Atomic replay of a dead letter queue, so an entry is never in neither destination.
- Secrets in the environment rather than in configuration files, and never in logs.