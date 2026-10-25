# Scope: Meta Hackathon Demo (End to End)

A repeatable demo showing normal agent action and unsafe action blocked/with approval, observable security event, and measurable evaluation results. Leverages existing Meta routing, tool execution with RBAC, policy checks (prompt injection/PII), Tripwire, Splunk telemetry, and red team concepts. No new credentials added; read-only inspection and documentation only.

**Build approach:** Tracer Bullet (vertical slice)
**Workflow:** /architect → review → (no implementation)

## Goal
Demonstrate:
1. Normal agent action: authenticated request through `/v1/meta/chat/completions` (or via public API path) reaching Meta/Baseten if configured, with PII redaction telemetry and normal completion.
2. Unsafe action blocked: prompt injection blocked by gateway guardrails with `agentshield.security.prompt_injection_blocked` event to Splunk; or tool execution blocked by RBAC (`tools:execute`) returning 403 with authz failure telemetry.
3. Observable security event in Splunk (tenant-scoped, content-free).
4. Measurable evaluation results (eval_passed / quality metrics visible in logs/usage if applicable).

## Existing components (to inspect)
- Meta routing: `gateway/routers/meta_models.py` - prompt injection scan, PII redaction, emits events, routes to Baseten
- Tool execution: `gateway/main.py:/v1/tools/execute` requires `tools:execute` scope (RBAC), calls MCP
- Policy checks: `config/guardrails/scanner.py` (prompt injection), PII redaction
- Tripwire: mounted verdict at gateway, health reports `file_integrity`
- Splunk telemetry: Redis stream → aggregator → Splunk HEC, events include security events
- Red team: no dedicated harness found in repo; concepts exist (injection/PII)

## Missing / gaps
- Red team test harness endpoints not present as named endpoints
- Demo needs concrete scenario with observable results and evaluation metrics
- May depend on model access (BASETEN_API_KEY) for normal path; unsafe path works without it

## Dependencies
- Auth0/JWT or dev token for auth
- Baseten key optional for blocked path; required for live completion
- Splunk/Redis running for events
- Tripwire mounted

## Done when
- Scope + spec written and reviewed before implementation
- Scenario documented with concrete commands, expected outputs, events, and eval metrics
- Clear mapping to existing code paths and gaps identified
