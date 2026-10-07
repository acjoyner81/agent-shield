# CA Refresh Runbook

**Spec**: 0014 — Land telemetry in Splunk reproducibly and visibly  
**Author**: primary agent (verify-driven)  
**Last verified**: 2026-10-07  

---

## Overview

Splunk can regenerate its certificate authority (CA) key during routine boot. When this happens, consumers that trust Splunk's own CA will stop shipping telemetry until the shared CA anchor file is refreshed. This runbook documents how to detect a rotation, refresh the anchor, and verify recovery.

---

## 1. Detecting a Rotated CA

The following symptoms indicate the shared CA anchor may have rotated:

| Symptom | Evidence |
|---|---|
| **Gateway health surface degraded** | `GET /v1/health/services` returns `splunk` entry with `status: degraded` and `detail` containing "certificate" or "TLS" |
| **Aggregator silent failures** | Aggregator logs show `ShipRejected` or connection errors; `telemetry:dlq` begins growing |
| **Health probe raises TLS error** | Primary agent's verify run reports `CERTIFICATE_VERIFY_FAILED` or `self signed certificate` |

**Immediate check**: Run the health probe:

```bash
docker compose exec gateway-python python3 -c "
from gateway.main import _probe_splunk
import asyncio
try:
    asyncio.run(_probe_splunk())
except RuntimeError as e:
    print('DEGRADED:', e)
"
```

If the reason contains 'certificate' or 'CA', the anchor likely rotated.

---

## 2. Refresh Procedure

### 2.1 On the Splunk container host

Run the entrypoint wrapper's CA generation logic (idempotent — reuses existing CA if unchanged):

```bash
# Inside the agent-shield project root
sudo bash /sbin/entrypoint-wrapper.sh 2>&1 | tail -20
```

The wrapper ( `splunk/entrypoint-wrapper.sh` ) will:
1. Reuse the existing CA if its fingerprint + passphrase hash match (no reissue)
2. Or generate a new CA with SANs `DNS:splunk, DNS:localhost, IP:127.0.0.1` and encrypt it with `SPLUNK_PASSWORD`
3. Publish the public certificate (`ca.crt`) to `splunk-ca:/opt/splunk/ca-share/ca.crt`

**What this does NOT do**: restart any consumer containers. The wrapper only writes to the persisted etc volume and the shared volume.

### 2.2 Restart consumers (recommended)

After the CA is refreshed, restart the consumer containers so they pick up the new anchor:

```bash
docker compose restart gateway-python telemetry-aggregator
```

**Why**: The gateway and aggregator cache the CA path at startup. A restart forces them to re-read `SPLUNK_HEC_CA` and re-verify the collector cert.

**Why NOT restart unnecessarily**: If the CA did NOT actually rotate, restarting is unnecessary work. The wrapper only reissues the CA when the fingerprint + passphrase hash changes, so a reissue implies a real rotation.

### 2.3 Verify recovery

After restarting consumers, verify:

```bash
# Gateway health should show splunk healthy
docker compose exec gateway-python python3 -c "
from gateway.main import _probe_splunk
import asyncio
try:
    asyncio.run(_probe_splunk())
    print('HEALTHY: splunk collector reachable')
except RuntimeError as e:
    print('STILL DEGRADED:', e)
"

# Aggregator should be shipping again
docker compose logs -f telemetry-aggregator | grep -i 'shipped\|moved\|DLQ' | tail -5
```

---

## 3. Verification Query

Run this Splunk search to confirm events are landing after the refresh:

```bash
curl -sk -u "admin:$SPLUNK_PASSWORD" \
  -d output_mode=json \
  --data-urlencode 'search=search index=main sourcetype=agentshield:telemetry earliest=-5m | head 5' \
  https://localhost:8089/services/search/jobs/oneshot
```

Expected: a JSON array of event objects with `event_id`, `tenant_id`, `data` fields. If empty, the collector is still not accepting events.

---

## 3. Tripwire Re-baseline (Deliverable 3)

**Coordinate with primary agent** before running. This re-baselines Tripwire's integrity baseline so it includes the current week's commits.

**Timing**: Run after the primary agent confirms the Splunk ACs are verified (so the health surface is stable).

**Steps**:
1. On the Tripwire controller, run the re-baseline tripwire config to include current file hashes
2. Verify `/v1/health/services` shows `file-integrity` as `healthy`
3. Confirm the drift only reappears when tracked files actually change

**Do NOT run** this deliverable during the primary agent's verify run, as it may confuse the health surface readings.

---

## 4. Java Gateway Scoping (Deliverable 4)

Run `/scope` for the Java Gateway core logic. This is a separate scope item that does not overlap with the Splunk verify run.

**Steps**:
1. Run `/scope` in the repo root
2. Review the Java Gateway feature flag and design decisions
3. Note: Tripwire monitors `./java-services/`, so any Java source changes must trigger a Tripwire re-baseline (see Deliverable 3)

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-10-07 | Initial runbook created | primary agent |
| 2026-10-07 | Verified on fresh `docker compose up` | primary agent |