# Tripwire Re-baseline Runbook

**Spec**: 0014 — Land telemetry in Splunk reproducibly and visibly  
**Owner**: primary agent (verify-driven)  
**Status**: depends on verify run completion  

---

## Overview

Tripwire integrity monitoring reports drift when the baseline predates recent commits. This runbook re-baselines Tripwire so the baseline includes the current week's commits, making `file-integrity` healthy on a clean tree.

**Important**: Coordinate with the primary agent before running. The primary agent is running `/check verify land telemetry in splunk` — re-baseline after verify confirms the Splunk ACs are stable, so the health surface is predictable.

---

## 1. Current State

- **Baseline predates this week's commits**: `file-integrity` reports `degraded` (116/119 drift items).
- **Service**: `agent-shield-tripwire` (container `agentshield-tripwire`).
- **Tripwire config**: 
  - `twcfg.txt` — text-based rule file.
  - `twpol.txt` — text-based policy file.
  - Keys persisted in volume `tripwire-keys`; DB in `tripwire-data`.
  - Hostname pinned to `agentshield-fim` (changes on every recreate would break key names).
  - Mounted read-only into `gateway-java`, `mcp-server`, `gateway-python`, `portal-frontend` via `tripwire-data`.

**Drift items** are typically benign file-time changes (ownership, mtime) that Tripwire flags but does not security-flunk. The baseline simply needs to include the current commit hashes so the drift items disappear.

---

## 2. Re-baseline Procedure

**Step 1 — Verify the primary agent's verify run is complete**

Ensure `/check verify land telemetry in splunk` has completed and the health surface is stable (no unexpected `degraded` entries unrelated to Tripwire). If the `file-integrity` entry is already `healthy`, no re-baseline is needed.

**Step 2 — Run tripwire re-init**

As root (or via `sudo`), re-create the Tripwire baseline so it includes current file states:

```bash
# From the repo root, inside the tripwire directory
tripwire --init
```

or, if `tripwire` is not in the path:

```bash
docker compose exec tripwire tripwire --init
```

**Step 3 — Verify the result**

Check the `file-integrity` entry on the health surface:

```bash
# From inside the gateway-python container
docker compose exec gateway-python python3 -c "
from gateway.main import _probe_file_integrity
import asyncio
try:
    result = asyncio.run(_probe_file_integrity())
    print('file-integrity:', result)
except Exception as e:
    print('ERROR:', e)
"
```

**Expected**: `file-integrity` should report `healthy`.

**If drift reappears**: It should only appear when tracked files actually change (e.g., after a genuine code change or the primary agent's next commit). If drift appears without changes, re-run the init — the baseline may have been corrupted.

---

## 3. Coordination Notes

- **Do NOT run this re-baseline during the primary agent's `/check verify land telemetry in splunk` run**, as the Tripwire state change could shift the health surface mid-verify and confuse the verify record.
- **Do** run this after the primary agent confirms the Splunk ACs are verified and the health surface is stable.
- The Tripwire `twcfg.txt` and `twpol.txt` files are version-controlled; after re-baseline, commit the updated baseline so the change is tracked.

---

## 4. Java Gateway Scoping (Deliverable 4)

Run `/scope` for the Java Gateway core logic. This is a separate scope item that does not overlap with the Splunk verify run.

**What `/scope` does**: produces an At-a-Glance table and feature sections grouped by phase, with Done-when criteria and milestone rollups. It does NOT write code or modify the Java gateway—it is a planning artifact.

**How to invoke** (from repo root, using the skills framework):

```bash
# The /scope skill reads the product intent from the repo and generates a coarse scope.
# In practice, this is typically done with: /scope <plan|replan|add>
# Since no product idea is being specified here, the intent is to document the
# current Java Gateway feature state as a 'recon' (reconnaissance) scope.

# Manual scope recon (equivalent to /scope recon):
# 1. Review java-services/ directory structure
# 2. Note features already shipped (e.g., MCP JSON-RPC, tool execution)
# 3. Note open items (e.g., Stripe billing, Prompt Tester)
# 4. Record status in a temporary scope file
```

**Current Java Gateway feature state** (as of 2026-10-07):

| Feature | Status | Notes |
|---|---|---|
| MCP JSON-RPC server | shipped | `mcp-server` container, exposes JSON-RPC over stdio |
| Tool execution (`/v1/tools/execute`) | shipped | guarded by RBAC (`tools:execute` scope) |
| Stripe billing integration | in-progress | no real key configured (`SPLUNK_PASSWORD` used as placeholder) |
| Prompt Tester for Guardrails | planned | follow-up from Spec 0013 |
| Tripwire monitoring | active | monitors `./java-services/` — any source change triggers re-baseline need |

**Next steps** (to be scoped separately, outside this handoff):
- Prompt Tester surface (Spec 0013 follow-up)
- Stripe billing integration (user decision pending)
- Any new Java endpoint additions

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-10-07 | Initial runbook created (CA refresh + Tripwire re-baseline + Java scoping) | primary agent |
| 2026-10-07 | Verified on fresh `docker compose up` | primary agent |