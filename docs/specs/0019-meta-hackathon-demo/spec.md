# Spec 0019: Historical Draft

This is the original documentation-only draft. It is retained for history and is not the current acceptance criteria. Use [index.md](./index.md) for the current buildable integration spec.

## Overview
Define a repeatable demo scenario using existing AgentShield capabilities.

## Scenario (concrete)
### Normal agent action
1. Authenticate (bearer token) with tenant context
2. Call `POST /v1/meta/chat/completions` with benign prompt + email? Or show PII redaction path
   - If `BASETEN_API_KEY` configured: request forwarded to Baseten after redaction; response sanitized
   - Telemetry: normal flow, possible `agentshield.security.pii_redacted` if PII present
3. Observable: HTTP 200, response from model (or stub behavior), trace_id

### Unsafe action (blocked)
Option A: Prompt injection
- Call `/v1/meta/chat/completions` with injection pattern
- Blocked at ingress: HTTP 400 "Prompt rejected by AgentShield ingress guardrails: potential injection detected."
- Event: `agentshield.security.prompt_injection_blocked` emitted (tenant_id, model, level) - content-free
- No upstream call

Option B: Unauthorized tool execution
- Call `POST /v1/tools/execute` without `tools:execute` scope
- RBAC returns 403 with `require_permission` detail; authz failure event emitted
- Demonstrates policy enforcement + approval requirement (403 = blocked/requires permission)

## Observable security events
- `agentshield.security.prompt_injection_blocked` (meta router)
- `agentshield.security.pii_redacted` (meta router)
- authz failure events (RBAC) via telemetry

## Measurable evaluation results
- Health: `/v1/health/services` shows splunk, file_integrity (Tripwire)
- Telemetry/logs: events appear in Redis stream / Splunk index (async)
- Usage/quality: if eval_passed populated, visible in logs/usage summary
- Gateway metrics/events observable

## What works
- Meta router with injection scan, PII redaction, event emission
- RBAC on tools/execute
- Tripwire verdict mounted, health integration
- Telemetry pipeline to Splunk
- Existing demo doc `docs/demo/meta-hackathon-2026.md`

## Missing
- Named "red team test harness endpoints" - not present; injection/PII already act as tests
- Automated eval results aggregation for this demo scenario
- Credential provisioning docs for hackathon

## Dependencies
- Auth token (dev or real)
- Optional: BASETEN_API_KEY for live model call
- Redis/Splunk for event flow

## Acceptance criteria
- [ ] Scope doc created
- [ ] Spec doc created with scenario, commands, expected results, events, eval metrics
- [ ] Mapping to existing code paths documented
- [ ] Gaps identified (no broad refactor)
- [ ] Stopped for review before implementation
