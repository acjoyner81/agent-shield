# 0014. Land telemetry in Splunk reproducibly and visibly

**Date**: 2026-10-02
**Status**: Proposed

## Summary

Telemetry has never once reached Splunk. The gateway queues events in Redis and a
separate aggregator tries to send them, but it posts to a plain HTTP address while
Splunk only answers on a TLS port (the encrypted version of HTTP), so every attempt is
dropped before it is ever authenticated. This decision makes the path actually work,
makes it reproducible from the repo rather than living inside one running container,
and puts it on the health page so a failure like this cannot hide again. It also gives
operators a way to replay the 366 events already stranded.

Reasoning, options, and the evidence behind this: see `rationale.md`.

## Requirements

**User stories**:
- As an operator, I want events to actually land in Splunk so that searching my
  observability platform returns something.
- As an operator, I want a fresh machine to reach the same working state from the repo,
  so I never debug a hand made container edit again.
- As an operator, I want the health page to tell me when shipping is broken, so I stop
  learning it from a growing pile of undelivered events.
- As an operator, I want to replay the events that were stranded, so a three day outage
  does not become three days of permanently lost telemetry.
- As a security reviewer, I want personal data masked before it reaches a log platform,
  so shipping telemetry cannot leak it.
- As a tenant, I want another tenant's telemetry volume and failures not to be visible to
  me through a shared health endpoint.

**Acceptance criteria** (the contract):
- **AC-1**: An event written to `telemetry:queue` appears in the Splunk index within the
  batch window (5 seconds, or immediately at 100 entries), proven by searching Splunk
  for a known event id.
- **AC-2**: The aggregator posts over TLS and validates Splunk's certificate against
  Splunk's own certificate authority, whose path is
  `/opt/splunk/etc/auth/cacert.pem`. Verification covers the hostname `splunk` as well as
  the chain, so a certificate authority that is correct but names the wrong host still
  fails. A wrong or tampered certificate authority makes shipping fail loudly, and
  verification is never silently skipped.
- **AC-3**: `docker compose up` on a clean checkout produces a Splunk with the HTTP Event
  Collector already enabled and events landing, with no manual dashboard step and no
  hand edited container files.
- **AC-4**: Splunk's configuration and its stored event data both survive
  `docker compose up --force-recreate splunk`.
- **AC-5**: `POST /v1/telemetry/logs` enqueues to `telemetry:queue` and no longer posts to
  Splunk itself, so exactly one code path in the repository reaches the event collector.
  The `event_id` it returns is a real event identifier minted by the same emitter every
  other telemetry producer uses, so it can be traced through the queue and the dead letter
  queue.
- **AC-6**: Events are scrubbed of personal data before they reach Splunk. A JSON web
  token, an email address, and a `sk_live_` style key inside a payload all come out
  masked, and the masked text is what is stored in Splunk.
- **AC-7**: `GET /v1/health/services` carries a `splunk` entry. It is an HTTP POST to the
  collector URL with an empty body and the token, so polling never writes to the index.
  It reports healthy when the collector is reachable and is not stalled. It reports
  degraded, naming the reason, when the collector cannot be reached, when a ship has been
  attempted and has not succeeded inside the window, or when the queue holds events and no
  ship has ever succeeded. A stack with an idle queue and no failures is healthy.
- **AC-8**: `telemetry:dlq` has a cap. Trimming and incrementing the dropped counter happen
  in the same atomic script as the push that caused the overflow, so a count can never
  drift from the data. Both the current depth and the dropped total appear on the health
  surface, and only to callers holding `telemetry:admin`.
- **AC-9**: An endpoint gated by a `telemetry:admin` scope replays the dead letter queue
  back onto the stream in bounded batches, and the 366 currently stranded events can be
  drained to zero.
- **AC-10**: No redriven event is ever lost. Every writer pushes the dead letter queue at
  one end, and replay pops that same end, so a crash between reading the dead letter queue
  and writing to the stream leaves each entry in exactly one of the two, proven by
  interrupting the operation.
- **AC-11**: Ship outcomes record a machine readable reason and a consecutive failure
  count in Redis. A failed ship is never logged as an empty string again, and a rejected
  event is never reported as a successful ship.
- **AC-12**: A collector response whose HTTP status is 200 but whose body reports a non
  zero code is treated as a failed batch. Those events reach the dead letter queue instead
  of being acknowledged and lost, which is what the current code does today.

## Decision

**Chosen option**: Option 1: Fix in place, keep the aggregator, make the deployment and the
transport reproducible, and collapse the extra shipping paths onto the one that works.

Events keep flowing through the existing Redis stream and the existing aggregator service.
The scheme, the certificate trust, and the deployment are corrected in place, the two
duplicate shipping paths are retired rather than migrated, and the missing operator
surfaces (health entry, replay, dropped accounting) are added.

## Feature design

**Data model sketch**:

Redis, all under the existing `telemetry:` namespace. No new database, no schema migration.

| Key | Type | Fields | Rule |
|---|---|---|---|
| `telemetry:queue` | Stream | `payload` (string, required) | Exists today. The only ingest path after AC-5. |
| `telemetry:dlq` | List (capped) | raw event JSON strings | Exists today. **Every writer pushes at the same end** and replay pops that end. Cap and drop accounting added by AC-8. |
| `telemetry:ship` | Hash | `last_attempt` (epoch seconds), `last_success` (epoch seconds, absent until the first success), `last_error` (string), `consecutive_failures` (integer) | New. Written by the aggregator on every batch outcome, attempt and success alike. Powers AC-7 and AC-11. |
| `telemetry:dlq:dropped` | Integer counter | count | New. Incremented in the same script as the trim. Powers AC-8. |
| `telemetry:redrive:lock` | String with a 30 second expiry | holder id, a `uuid4` | New. Exists to return 409 to a second replay, not to make replay safe, which the atomic pop already guarantees. Released explicitly in a `finally`, and the expiry covers a holder that dies. |

**Push direction is a correctness requirement, not a style choice.** Today
`gateway/aggregator.py:148` pushes with `RPUSH` while `gateway/metering.py:166,265` push
with `LPUSH`, so the queue holds entries at both ends. A replay that pops one end can
never reach the other, which makes AC-9's drain to zero unreachable and leaves the trim
boundary undefined. This spec standardises on `RPUSH` everywhere, replay uses `RPOP`, and
the cap is `LTRIM telemetry:dlq -N -1`, keeping the most recent N. Changing
`gateway/metering.py` is therefore in scope, not a follow up.

**Splunk volumes** (named volumes, both required by AC-4):

| Path | Holds | Also consumed by |
|---|---|---|
| `/opt/splunk/etc` | Splunk configuration, including the certificate authority and the generated keys | Splunk only. Never mounted into another container. |
| `/opt/splunk/var` | Indexes, saved searches, `splunkd.log` | Splunk only |

**The certificate authority is shared as a single file, not as a directory mount.**
`/opt/splunk/etc/auth` also holds private key material (`distServerKeys/private.pem`,
`splunkweb/privkey.pem`), so mounting the whole of `/opt/splunk/etc` read only into the
gateway and the aggregator would hand a Splunk private key to two other services. Instead
the authority file is copied to a separate shared volume holding only
`cacert.pem`, and that single file is what the consumers mount. This also survives a Splunk
container recreate unchanged, since it does not depend on the collector's container layer.

**State transitions** (event lifecycle):

```
produced -> telemetry:queue -> shipped (code 0, receipt written) -> acked
                          \-> attempts exhausted -> telemetry:dlq -> acked
telemetry:dlq --replay (one atomic script)--> telemetry:queue
```

The acknowledgement is the load bearing part and it happens last, in this order: attempt
the ship, and only when the collector reports code 0 **or** the events are in the dead
letter queue. Today the code acknowledges unconditionally after five attempts, which is
only safe because the dead letter copy exists. Treating an HTTP 200 with a non zero body
code as a success is what breaks that, because it acknowledges with no dead letter copy.

The replay arrow is one atomic Redis script, not two operations. This mirrors what
`gateway/metering.py` already does when it claims an event and applies the rollup in a
single script, after claiming first and writing second stranded a failed event and made
its dead letter entry redrive as a duplicate. The same failure would recur here.

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/v1/telemetry/logs` | POST | existing `TelemetryPayload` | `status` (`accepted`), `event_id` | existing (unchanged) | 422 |
| `/v1/telemetry/redrive` | POST | `limit` (integer, optional, default 100, max 1000) | `moved`, `remaining`, `dropped_total`, `last_error` | Bearer JWT or API key, requires `telemetry:admin` | 403 missing scope, 409 replay already running, 200 with `moved: 0` when Splunk is still unreachable |
| `/v1/health/services` | GET | none | new `splunk` entry, plus a `metrics` object holding `dlq_depth` and `dlq_dropped` | existing; the `metrics` object only for `telemetry:admin` | none |

Both telemetry routes are operational and are excluded from the published contract, so
`/v1/telemetry/redrive` must be added to `excluded_paths` in `custom_openapi` and declared
`include_in_schema=False`, exactly as `/v1/telemetry/logs` already is. Omitting that puts
the route in `/docs` and breaks the schema regression test.

`POST /v1/telemetry/logs` keeps its existing response body and gains `event_id`, which
lets a caller trace one event through the queue or the dead letter queue. It produces that
id by going through the same `emit_event` path every other producer uses, with a `type` from
the valid event type list, rather than inventing an identifier at the route. Its
`TelemetryPayload.timestamp` is an optional float with no defined unit and is not used;
the envelope owns the clock.

`/v1/telemetry/redrive` reports `moved: 0` with the collector's last error rather than
failing when Splunk is still down. A replay into a broken collector would just refill the
dead letter queue, so the endpoint tells the operator why nothing moved instead of
pretending to work. `moved` is `min(limit, depth)` and `remaining` is the length read
after the script, on the same client.

**Value sourcing**:

| Action | Value produced / displayed | Source |
|---|---|---|
| Ship batch | Splunk payload | The `payload` field read from `telemetry:queue` |
| Ship batch | outcome | The collector response **body**, parsed for `code`; an HTTP status alone is not the outcome |
| Ship batch | `last_attempt` | The gateway's own UTC clock, `time.time()`, written before each attempt |
| Ship batch | `last_success` | The same clock, written only when the response body reports code 0 |
| Ship batch | `last_error` | The rejection reason: the non zero body code with its message, or the exception type and text, rendered non empty by AC-11 |
| Ship batch | `consecutive_failures` | Incremented per failed batch, reset to 0 on success |
| Ingest route | `event_id` | `EventEnvelope.event_id`, minted by `emit_event` |
| Ingest route | envelope `type` | A member of the valid event type list |
| Ingest route | envelope `timestamp` | The envelope's own UTC clock; `TelemetryPayload.timestamp` is unused |
| Health probe | `splunk.status` | Derived from collector reachability and the ship age rule below |
| Health probe | `detail` when degraded | Either the probe exception, or the literal reason the ship age rule fired |
| Health probe | `dlq_depth` | `LLEN telemetry:dlq` |
| Health probe | `dlq_dropped` | `GET telemetry:dlq:dropped` |
| Redrive | `moved` | Entries transferred by the atomic script, `min(limit, depth)` |
| Redrive | `remaining` | `LLEN telemetry:dlq` read after the script, on the same client |
| Redrive | `dropped_total` | `GET telemetry:dlq:dropped` |
| Scrub | masked payload | `scrub_pii` over each string value, applied to the payload before the POST |

**The health rule, stated so it has no unstated cases.** Degraded if the collector cannot
be reached, **or** a ship has been attempted and its age exceeds
`SPLUNK_HEALTH_MAX_SHIP_AGE_SEC`, **or** the queue is non empty and no ship has ever
succeeded. Healthy otherwise, which means an idle queue with no failures is healthy and a
cold start with an empty queue is healthy. Ages are compared with a strict `>` against the
gateway's UTC clock, matching the existing file integrity probe. Without the `last_attempt`
field and the third clause, a stack with nothing to ship would report degraded within the
window and flip the overall status on every route.

**Key invariants**:
- No event leaves `telemetry:queue` until the collector reports code 0 **or** the event is
  in `telemetry:dlq`. The acknowledgement comes last, never instead.
- A response body reporting a non zero code is a failure, never a success.
- Every replayed event exists in exactly one of the two lists at all times, and every
  writer pushes the dead letter queue at the same end.
- The dead letter counter is incremented in the same script as the trim that caused it.
- Verification of the collector's certificate is never disabled by configuration.
- Exactly one module in the repository performs an HTTP call to the collector.
- No Splunk private key is readable by any service other than Splunk.
- Personal data scrubbing runs before the POST, not after, so nothing unmasked ever
  reaches the network.

**Security model**:
- The collector token stays a secret environment variable. It is never written to the
  health surface, never logged, and dead letter queue payloads are scrubbed with the same
  patterns, so a token that reached a telemetry payload does not leak from the replay path
  either.
- The shared certificate authority volume holds exactly one public file. Nothing that can
  sign or decrypt leaves the Splunk container.
- `telemetry:admin` is a new scope, separate from `keys:write`. Replaying every tenant's
  logs is a different privilege from minting credentials, and the key write gate already
  only grants scopes the caller holds, so an admin can mint keys with `telemetry:admin`
  without a second rule.
- The scope must be added to `DEFAULT_PERMISSIONS` in `auth0/seed_and_verify.py` and to the
  contract table in spec 0012, or it exists in code but cannot be granted to anyone.
- **The dead letter queue is a shared multi tenant aggregate.** Its depth and dropped total
  therefore reveal fleet wide telemetry volume and failure history, so they are exposed
  only to `telemetry:admin`, in a separate `metrics` object rather than mixed into the
  per service entries. The `splunk` entry itself is reachability and ship age only, which
  is platform state with no tenant data, consistent with the existing probe docstring and
  safe for any tenant member to see.
- The new scope is not available to tenants by default, so shipping telemetry stays a
  platform operator action rather than a tenant one.

**Configuration required**:
- `SPLUNK_HEC_URL`: defaults to `https://splunk:8088/services/collector/raw`. The scheme is
  the load bearing part; `http` is the bug being fixed.
- `SPLUNK_HEC_TOKEN`: existing secret, unchanged.
- `SPLUNK_HEC_CA`: path to the mounted certificate authority file. A missing path makes the
  aggregator refuse to start, but only after the compose health gate has released, because
  Splunk generates its certificate authority minutes into boot. The gateway does not
  hard fail; its probe reports degraded with the reason instead.
- `SPLUNK_HEC_CA_WAIT_SEC`: how long a consumer waits for that file before giving up,
  default `300`.
- `SPLUNK_HEC_INDEX`: default `main`, sent as the collector's index query parameter.
- `SPLUNK_HEC_SOURCETYPE`: default `agentshield:telemetry`, sent as the sourcetype query
  parameter.
- `SPLUNK_HEALTH_MAX_SHIP_AGE_SEC`: default `300`. Above this the probe reports degraded
  when a ship has been attempted, which is what catches a stalled pipeline.
- `TELEMETRY_DLQ_MAX`: default `10000`, the cap behind AC-8.
- `SPLUNK_ENABLED`: removed. It is currently in `.env` set to `false` and read by zero
  lines of code, so it reads as though shipping is deliberately off while it is actually
  on and failing.

**Critical test scenarios**:
- Happy path: an event written to the queue is searchable in Splunk within the batch
  window, verified by event id, verifies **AC-1**, **AC-6**
- Transport: with the URL set to `http`, shipping fails and `last_error` names the reset
  rather than rendering empty, verifies **AC-11**, **AC-2**
- Rejected event: the collector answers HTTP 200 with a non zero code, and the batch is
  asserted to be in the dead letter queue and not acknowledged away, verifies **AC-12**
- Certificate trust: with a wrong certificate authority mounted, the aggregator fails the
  ship and does not skip verification; with a certificate authority that does not cover
  the hostname `splunk`, it also fails rather than proceeding, verifies **AC-2**
- Secret containment: assert no file readable by the gateway or the aggregator contains a
  Splunk private key, verifies **AC-2**
- Reproducibility: destroy the stack and bring it up from a clean checkout, events land
  with no manual step, verifies **AC-3**, **AC-4**
- Idle stack: leave the queue empty with no failures and assert the health entry stays
  healthy past the window, verifies **AC-7**
- Stalled pipeline: stop the aggregator while Splunk stays up, and assert the health entry
  turns degraded after the window and names the stalled ship, verifies **AC-7**
- Failure case: Splunk is stopped, five attempts fail, the batch lands in the dead letter
  queue and is still present after a restart, verifies **AC-8**, **AC-10**
- Replay under interruption: kill the process mid replay, then confirm the dead letter
  depth plus queue depth accounts for every event with none in neither, verifies **AC-10**
- Auth/permission: a token without `telemetry:admin` gets 403 naming the scope, and
  without it receives no dead letter counts from the health endpoint, verifies **AC-9**
- Concurrent replay: two simultaneous replay calls, one gets 409 and the other completes,
  verifies **AC-10**
- Contract surface: assert `/v1/telemetry/redrive` is absent from the published schema,
  verifies **AC-9**

## Build plan

Ordered as a thin end to end thread first, per the project's Tracer Bullet approach in
`docs/scope/scope.md`. Task 5 is the first moment anything works end to end and is proven;
everything before it is a prerequisite and everything after it thickens a path that
already runs.

1. Add the two named volumes to `splunk`, and the mechanism that enables the collector on
   every boot from a repo owned config file, satisfies **AC-3**, **AC-4**
2. Publish the certificate authority as a single file on its own shared volume, and issue
   the collector certificate with a subject alternative name covering `splunk` and
   `localhost`, satisfies **AC-2**
3. Change the collector URL to `https`, mount that file into the aggregator and the gateway,
   wait for it behind the compose health gate rather than failing at boot, and construct the
   HTTP client to verify against it, satisfies **AC-2**
4. Write the ship receipt on every attempt and outcome, including `last_attempt`, and render
   a non empty failure reason, satisfies **AC-11**
5. Prove one real event lands in Splunk by searching for its id, satisfies **AC-1**
6. Parse the collector response body and route a non zero code to the dead letter queue
   instead of treating it as success, satisfies **AC-12**, **AC-11**
7. Call the existing `scrub_pii` over string values in the shipping path, with tests for a
   JSON web token, an email address, and an `sk_live_` style key, satisfies **AC-6**
8. Standardise the dead letter queue on one push end across `aggregator.py` and
   `metering.py`, add the atomic trim plus counter, verifies **AC-8**, **AC-10**
9. Make `POST /v1/telemetry/logs` enqueue only through the shared emitter, and delete or
   redirect the other two collector call sites, checking `observability/splunk_exporter.py`
   for callers first, satisfies **AC-5**
10. Add the `splunk` health entry using reachability plus the ship age rule, and add the
    `metrics` object for the dead letter counts, gated to `telemetry:admin`, satisfies
    **AC-7**, **AC-8**
11. Add the `telemetry:admin` scope, the atomic replay script, and the redrive endpoint,
    excluded from the published schema, then seed the scope and document it in spec 0012,
    satisfies **AC-9**, **AC-10**
12. Drain the 366 stranded events and confirm the queue reaches zero, satisfies **AC-9**

## Migration plan

**Strategy**: strangler. Each phase is independently reversible by reverting one commit,
except the Splunk recreate in phase 1, which is a one way cutover by design.

**Phases**:
1. **Storage and enablement.** Add the two named volumes and the repo owned config that
   enables the collector. Recreate `splunk` once. This is the only destructive step: the
   current container holds no event data, and the hand written `[http] disabled=0` override
   is intentionally discarded and replaced by the repo owned file. Verify the collector
   answers HTTPS before going further.
2. **Transport.** Publish the certificate authority file, then deploy `https` plus the
   mount. Events begin landing. At this point the gateway still posts inline to its own
   broken URL, which fails harmlessly, so nothing regresses for existing callers.
3. **Single path.** Make `POST /v1/telemetry/logs` enqueue only and retire the other two
   call sites, deploy, then confirm exactly one module reaches the collector.
4. **Operator surfaces.** Ship the ship receipt, the health entry, the dead letter cap, and
   the redrive endpoint behind `telemetry:admin` once the scope is seeded.
5. **Replay.** Drain the 366 stranded events. Separate from the cutover on purpose, so a
   replay problem cannot block shipping from working.

**Rollback**: phases 2 through 5 revert by redeploying the previous image. Events keep
queueing in Redis throughout, so nothing is lost by reverting. Phase 1 cannot be reverted
by redeploying, because recreating `splunk` with a volume starts from an empty
configuration; the way back is removing the volumes and starting the container without
them, which returns to the hand made override being required.

**Risks**:
- If Splunk regenerates its certificate authority, consumers stop shipping until the
  authority file is refreshed. This is the main new operational chore and it needs a
  documented procedure.
- The certificate authority is produced minutes into Splunk's boot. A consumer that fails
  hard before waiting is the difference between a slow start and a crash loop, which is why
  the wait is explicit and bounded rather than implicit.
- Enabling the scrubber changes stored payload text, so existing searches or dashboards
  built on unmasked values will not match after phase 3.
- Changing the dead letter queue push direction reorders existing entries, so the 366
  stranded events move position within the list. Their content is unaffected.
- The health entry can go degraded for reasons that are not Splunk's fault, namely a
  stalled aggregator. That is intended, but it is a new alert surface and should be tuned
  so it is not ignored.

## Consequences

**Positive**:
- Shipping works, and the first telemetry this platform has ever delivered.
- A fresh machine reproduces the working state, so this class of bug cannot recur silently.
- A broken collector is visible on the health page within five minutes instead of after
  three days, without reporting a healthy idle stack as broken.
- Stranded events are recoverable rather than permanently lost.
- Personal data is masked before it leaves the process.
- Rejected events stop being silently acknowledged and destroyed.

**Negative / tradeoffs**:
- Adding a volume means the first boot of the new configuration starts Splunk from an
  empty state, which discards the one hand made override that made the collector work
  today. That is intended, but it means the recreate is a real, visible cutover rather
  than a transparent swap.
- Certificate trust becomes something to operate. If Splunk regenerates its certificate
  authority, consumers stop shipping until the authority file is refreshed, which is a new
  operational chore this configuration did not have.
- Enabling the scrubber changes stored payloads. Anything already in an index, or any
  dashboard built on an unmasked value, will not match after this ships.
- A new permission scope has to be seeded, documented, and added to the contract table,
  which is more surface than reusing `keys:write` would have been.
- Two shipped paths are deleted, so any caller of `observability/splunk_exporter.py`
  outside this repository breaks. Task 9 checks for callers first.
- Standardising the dead letter push direction means editing the metering module, which was
  not in the original plan for this slice.

**Neutral**:
- The aggregator stays a separate container. With only one collector call site left, whether
  it should instead be a background task inside the gateway is now a legitimate question,
  recorded as follow up rather than decided here.
- The dead letter queue keeps its existing Redis list type; a trimmed stream would carry
  attempt counts, at the cost of a further change to both writers.

## Follow-up

- [ ] Spec 0002 AC-1 was ratified as met on 2026-09-14 and has never been true. Scope row 2
      (Redis to Splunk Aggregator) is marked `done` with Design, Build, Verify, and Test all
      ticked. `/develop` and `/sync` should reconcile that row and AC-1 once this spec ships,
      since both currently claim the aggregator works end to end.
- [ ] AgentShield API keys use the `sk_live_` prefix, which is Stripe's live secret key
      format. It caused a live Stripe key to be misread during this investigation, and it
      will trip secret scanners. Changing it to an unambiguous prefix is cheap now and
      expensive once real keys exist. Enrolled as scope feature 14, tagged `from spec 0014`.
- [ ] `gateway/debug_redis_connection.py:17` calls `LLEN` on `telemetry:queue`, which is a
      stream, so it always reads zero.
- [ ] Once events land, decide what Splunk searches, dashboards and alerts the platform
      actually needs. Shipping to a searchable index nobody queries is the next version of
      this problem.
- [ ] Consider whether the aggregator still needs to be its own container now that exactly
      one code path reaches the collector.
- [ ] Write down the procedure for refreshing the shared certificate authority file if
      Splunk regenerates it, so the recovery is not folklore.
- [ ] `docs/scope/scope.md` needed a feature row for this spec, since scope row 2 is narrower
      than this decision. Done: enrolled as scope feature 13 (Land Telemetry in Splunk), with
      a five milestone rollup of the build plan above.