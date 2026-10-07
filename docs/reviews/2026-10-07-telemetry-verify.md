# Verify: land telemetry in splunk (Spec 0014)

Date: 2026-10-07
Commit: `a2008f0` (= `origin/main`)
Mode: verify (runtime evidence against the 12 acceptance criteria in `docs/specs/0014-land-telemetry-in-splunk/index.md`)
Result: **FAIL.** 10 of 12 criteria are proven at runtime. AC-7 fails against its own contract. AC-10 is missing the interruption proof the criterion names.

No verify box was ticked. Feature 13 in `docs/scope/scope.md` stays in progress.

## Verdict per criterion

| AC | Verdict | Evidence |
|----|---------|----------|
| AC-1 | PASS | Cold boot ingest `evt_21ca14b7bd3a471fbffc17158b01f4b9`, envelope written 12:22:04.840, shipped 12:22:09.939 (batch window 5s), then found by Splunk search on the event id. Heal run `evt_6689bb9b377248098c142dd1c7bdc7dd` shipped inside the same window again. |
| AC-2 | PASS (deviation) | Ships over TLS with `SPLUNK_HEC_CA=/etc/agentshield/splunk-ca/ca.crt`, chain plus hostname `splunk` verified, missing CA raises SystemExit (test suite), live HEC health answers 200 under that verification. Deviation: the AC names `/opt/splunk/etc/auth/cacert.pem`, which holds Splunk's own encrypted CA key and cannot be exported; the wrapper generates its own CA with explicit SANs, recorded in AGENTS.md. The AC text is stale and needs amendment. |
| AC-3 | PASS (scope note) | The whole recipe is in repo files: compose, `splunk/entrypoint-wrapper.sh`, HEC env, CA volume. `docker compose down && up` and `--force-recreate splunk` both reached healthy with no manual step. The original empty volume first boot (stack creation) enabled HEC and created the CA; it was not re-exercised today because the volumes still hold state and wiping them would destroy the REST seeded saved searches. No hand edited container files exist. |
| AC-4 | PASS | `docker compose up --force-recreate splunk`: healthy in about 30s, HEC health `{"code":17}`, previously indexed events still searchable. |
| AC-5 | PASS | `POST /v1/telemetry/logs` (main.py:811) enqueues through `emit_event` and returns a real `event_id`. The deployed gateway container image contains `emit_event` and no direct `requests.post` to the collector. `GET /openapi.json` exposes no telemetry path. |
| AC-6 | PASS | Stored Splunk `_raw`: `contact [MASKED] token [MASKED] key [MASKED]` for the JWT, email, and `sk_live_` payload. |
| AC-7 | **FAIL** | The `splunk` entry exists, the probe never writes to the index (index count 2 to 2 across three polls), and degraded states carry a reason (live `ConnectTimeout` observed while HEC was slow, staleness reasons, never-succeeded-with-queue). Deviation: the probe GETs `/services/collector/health` instead of the POST with empty body the AC specifies, intent preserved and commented in code. **The failure:** with an idle queue and `consecutive_failures=0`, after 300s the probe reports `degraded: the last ship attempt is 3118s old, over the 300s limit (0 consecutive failures): no reason recorded`, while the AC's last sentence says a stack with an idle queue and no failures is healthy. Root cause pinned at `gateway/main.py:314`: the staleness check applies whenever a `last_attempt` exists, without asking whether the queue holds undelivered work. |
| AC-8 | PASS | Five atomic cap script tests in `test_aggregator.py` pass; live health surface returns `dlq_depth: 0, dlq_dropped: 0` for `telemetry:admin` and no metrics key for the unprivileged token; the redrive drained 582 events with `remaining: 0, dropped_total: 0`. |
| AC-9 | PASS | Admin gated redrive drained 582 stranded events to 0 (the AC's 366 was written against an older queue state; the behavior is proven at the current count). Unprivileged call returns `403 {"detail":"Permission denied: missing required scope 'telemetry:admin'"}` and the schema lists no telemetry route. |
| AC-10 | **GAP** | The design is one atomic Redis script (`REDRIVE_LUA`, single EVAL, so a crash cannot split an entry across the two stores), three redrive tests in `test_metering.py` (lines 88, 156, 323) pass, and the live drain lost nothing (`582` to `0`, `dropped_total 0`). The AC's literal proof is "interrupting the operation", and that test lived in `gateway/tests/test_telemetry_landing.py`, which is lost with only a `.pyc` remaining. Restore it. |
| AC-11 | PASS | The real `record_failure` run against live Redis wrote `last_error: "collector refused the batch: code=3"` and `consecutive_failures: 1`. The next ingest (`evt_6689...`) shipped and the success reset the receipts to an empty reason and `0` failures. |
| AC-12 | PASS | The real `ship_to_splunk` fed a stubbed HTTP 200 body `{"code":3,"text":"No data"}` raised `ShipRejected: collector rejected the batch: code=3 text='No data'`. The live collector cannot be coerced into a 200 with a non-zero body on demand, so the repository branch was executed at runtime rather than simulated. No committed test covers this branch. |

## Gaps and owners

1. **AC-7 defect, owner `/debug`:** idle stacks degrade after 300s despite zero failures. Fix at `gateway/main.py:314` by gating the staleness check on undelivered work (queue lag or pending entries), then re-verify. The AC's own POST wording also needs amendment to match the GET health probe.
2. **AC-10 interruption test, owner `/test`:** restore an interruption test for the redrive, and add a regression test for the AC-12 `ShipRejected` branch while there. Both were lost with `test_telemetry_landing.py`.
3. **Spec amendments owed:** AC-2 CA path, AC-7 probe method (GET) plus the idle health clause once fixed.
4. **Saved searches (other agent, Part 2):** all three exist live in Splunk (`Error_rate_by_data_path`, `Volume_by_tenant_id`, `Alert_for_agentshield_security_events`, seeded over REST), but `splunk/agent-shield-apps/default/savedsearches.conf` is inert: compose does not mount it, and `entrypoint-wrapper.sh` has no install step, while the file's header claims it is generated by the wrapper. Wire it into the boot path or drop the claim.

## Side findings during this run

- Tripwire was re-baselined twice. The other agent's 12:48 baseline captured the rewound tree (old `aggregator.py`, 7175 bytes); the 13:17:44 recovery to `a2008f0` (15010 bytes, plus `test_aggregator.py` and config changes) showed up as 16 legitimate violations. Re-run of `tripwire --init` with the local passphrase from `tripwire/entrypoint.sh` followed by the monitoring loop: `Total violations found: 0`, verdict `clean`, `file-integrity` healthy.
- `stripe` reports degraded (404 on `api.stripe.com/v1/`), pre-existing, real key decision pending.
- Two stale containers `splunk-certprobe` and `splunk-certprobe2` from Oct 5 sit unhealthy in `docker ps`; safe to remove.
- Backend suite `394 passed`, portal suite `172/172`, `docker compose config -q` all green on the verified tree.

## Update 2026-10-07 (after /debug and /test)

Both findings under "Gaps and owners" are closed the same day:

1. **AC-7 fixed** (commit `70e3ddb`). The staleness check is now gated on undelivered
   work through a new `_undelivered_events` helper in `gateway/main.py`: stream lag plus
   claimed but unacknowledged entries, falling back to `xlen` only for a stream with no
   consumer group. Live proof: the gateway image was rebuilt, and at 14:47:26 UTC, 357
   seconds past the last ship attempt (an idle queue, lag 0, pending 0, past the 300s
   window) `/v1/health/services` reported `splunk: healthy`. The identical state reported
   `degraded: the last ship attempt is 496s old...` before the fix. Five regression tests
   in `TestSplunkProbe` (`gateway/tests/test_dashboard_api.py`) pin the idle, waiting with
   a stale attempt, claimed but unacknowledged, cold start, and stale failure streak
   states.
2. **AC-10 proof restored** (commit `e8a1ef3`). `gateway/tests/test_telemetry_landing.py`
   is recreated with 6 tests: the happy drain, the batch limit, an interruption before the
   move (dead letter queue intact, stream empty), an interruption after the move (stream
   holds it, queue empty), 403 without `telemetry:admin`, and 401 without a credential.
3. **AC-12 now has committed tests.** Three in `test_aggregator.py`: `ship_to_splunk`
   raising `ShipRejected` on a nonzero body code, a rejected batch reaching the dead
   letter queue with the stream entry acked only after its copy is safe, and code 0
   shipping with no dead letter copy and cleared receipts.

Backend suite `408 passed`. The spec text for AC-2 and AC-7 was amended in
`docs/specs/0014-land-telemetry-in-splunk/index.md` the same day. Feature 13's `Verify it`
box is ticked on this original run plus these fixes, not on a second full verify pass: the
other ten criteria were unaffected by the changes above.

Side note from the same afternoon: `file-integrity` went degraded at 14:41:57 UTC with
"115 of 120 monitored objects differ". Investigation via `twprint` showed 110 of the 115
were inode number changes only (identical content and mtime, new inodes, consistent with
a Docker Desktop file sharing session remap during the check loop's stalls) and the
remaining 5 were the legitimate edits from this fix and the other agent's in flight work.
Approved with `docker compose exec tripwire /approve.sh --yes`: 0 violations remaining.
