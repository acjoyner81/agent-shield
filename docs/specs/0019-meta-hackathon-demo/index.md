# 0019. Meta Hackathon End to End Demo

**Feature:** 25 (Meta Hackathon E2E Demo)
**Status:** Accepted
**Type:** Demo and enablement
**Owner:** AgentShield
**Date:** 2026-10-23

## Summary

Use the Enterprise Agent Engine as the agent in one repeatable demo. It retrieves a synthetic account record locally, then sends the model request through AgentShield so the gateway can apply its existing guardrails and send content safe events to Splunk. Dynatrace tracing is a later improvement, not a dependency for this demo.

## Context

The Enterprise Agent Engine is currently a separate Python application. Its LangGraph workflow retrieves records from a small in memory synthetic account store and sends model calls directly through LiteLLM. Its FastMCP server currently uses stdio transport. AgentShield has a guarded Meta completion route and a Redis to Splunk telemetry pipeline, but the two projects are not connected.

The existing draft describes gateway requests with curl, but that does not show AgentShield protecting a real agent workflow. The demo should use the Enterprise Agent Engine for a vectorless RAG lookup while keeping the retrieval local. Only the model request needs to cross the AgentShield boundary for this smallest useful integration.

The demo must continue to use synthetic records only. The current `ACC-9021` example includes an email address and financial fields. No real customer or account data should be introduced. Dynatrace would help inspect service traces later, but it is not needed to prove the main security and telemetry flow. Splunk remains the destination for gateway security events.

## Requirements

**User stories**:

1. As a hackathon presenter, I want the Enterprise Agent Engine to answer a synthetic account question through AgentShield so I can demonstrate the gateway protecting a real agent workflow.
2. As a security reviewer, I want an unsafe request blocked and recorded without prompt contents so I can show that the gateway enforces policy and preserves privacy.
3. As an operator, I want the demo to fail visibly when AgentShield is unavailable instead of silently sending the request directly to another model provider.

**Acceptance criteria**:

**AC-1**: Running the documented RAG command for `ACC-9021` executes the existing `VECTORLESS_RAG` route, retrieves the record from the Enterprise Agent Engine's synthetic local store, and uses AgentShield for that route's model completion. The demo configuration does not change model routing for the `DIAGNOSTICS` or `GENERAL` routes.

**AC-2**: The Enterprise Agent Engine sends its `VECTORLESS_RAG` model completion to AgentShield `POST /v1/meta/chat/completions` with the configured tenant credential. If AgentShield rejects the request or is unavailable, the Engine reports failure and makes no direct provider request and no mock fallback response.

**AC-3**: The RAG request succeeds when AgentShield and the configured inference provider are available. The Enterprise Agent Engine displays its route, response, token counts when returned, a cost estimate only when the model price is known, and measured request latency. Unknown cost is shown as unavailable, not `$0`, and is not recorded as zero in `cost_usd` telemetry.

**AC-4**: The synthetic account contact email is redacted by AgentShield before the completion is sent upstream. Splunk receives the content safe `agentshield.security.pii_redacted` event. No event emitted for this request contains the raw account contact or prompt content.

**AC-5**: A separate direct request to the AgentShield Meta route using the documented injection example returns HTTP 400, does not call the upstream model, and emits `agentshield.security.prompt_injection_blocked` without prompt content. The Enterprise Agent Engine's own guardrail may block an injection before it reaches AgentShield, so this gateway test is not presented as an Engine initiated request.

**AC-6**: A request to `/v1/tools/execute` without `tools:execute` returns HTTP 403 and produces an authorization failure event with `required_permission`. This remains a separate gateway control demonstration, not the Enterprise Agent Engine's local RAG retrieval.

**AC-7**: The documented verification steps show the relevant security events in Splunk and the current service health from AgentShield. Dynatrace is not required or represented as active tracing in this demo.

**AC-8**: The demo uses only synthetic local records, stores credentials outside source control, and does not send prompts or account records to external tracing. The demo instructions leave external LangSmith tracing disabled.

**AC-9**: The demo documentation identifies the Enterprise Agent Engine as a local vectorless RAG example backed by synthetic records, not a connected production data source or a general purpose enterprise knowledge store.

**AC-10**: Gateway backed model routing is limited to the `VECTORLESS_RAG` route in this demo. The Engine's `DIAGNOSTICS` and `GENERAL` routes retain their existing model behavior.

## Options considered

### Option 1: Use the Enterprise Agent Engine as the agent client

Keep its local synthetic RAG lookup and route the completion request through AgentShield. Use direct AgentShield requests for the gateway injection and RBAC demonstrations.

**Pros**:

1. Demonstrates AgentShield in a real agent workflow without moving or duplicating the RAG store.
2. Keeps the integration to one model request boundary.
3. Reuses the existing LangGraph, RAG lookup, Meta gateway, guardrails, and Splunk path.

**Cons**:

1. The RAG tool itself remains local to the Enterprise Agent Engine and is not authorized by AgentShield.
2. The two projects need compatible local setup and credentials.
3. The current Engine client needs a gateway mode that fails closed rather than using direct provider fallbacks.

### Option 2: Keep the projects as separate demonstrations

Show the Enterprise Agent Engine and AgentShield independently, without routing the Engine's model requests through AgentShield.

**Pros**:

1. Requires no code changes across the two projects.
2. Has the smallest short term implementation effort.

**Cons**:

1. Does not demonstrate AgentShield protecting an agent's model request.
2. Leaves the end to end story split across disconnected workflows.

### Option 3: Put the RAG tool behind AgentShield

Expose the Enterprise Agent Engine retrieval tool over a network transport and make AgentShield execute it through its tool boundary.

**Pros**:

1. Demonstrates gateway control over both model and tool access.
2. Creates a clearer path toward a separately deployed agent service.

**Cons**:

1. Requires a new service connection and changes to MCP transport or gateway tool routing.
2. Expands the demo beyond the smallest useful integration.
3. Adds deployment and authentication work that is not necessary to prove the selected scenario.

## Decision

**Chosen option**: Option 1, use the Enterprise Agent Engine as the agent client.

Keep its existing local synthetic RAG lookup. Add a demo configuration that routes the Engine's model completion through AgentShield and fails closed if the gateway cannot serve it. Use direct gateway requests for the injection block and tool permission denial. Defer Dynatrace until the integrated request path exists and trace propagation can be validated across it.

## Rationale

The demo needs to show an agent request passing through AgentShield, not just place two projects beside each other. Routing the Enterprise Agent Engine's model request through the existing Meta gateway proves that the gateway can apply prompt checks, redact PII, and emit telemetry while the agent keeps its current retrieval flow.

Routing the RAG tool through AgentShield would add a separate MCP or service integration. That is a valid later direction, but it is more work than this demo needs. Dynatrace also has value for cross service timing, but the AgentShield tracing scaffold is not active, and the Engine's current LangGraph workflow uses its LangSmith or local tracer rather than its Dynatrace tracer. Adding Dynatrace before the service boundary is in place would create tracing setup without proving a complete distributed trace.

## Feature design

**Data model sketch**:

No persistent data model changes. The Enterprise Agent Engine continues to use its existing synthetic `ENTERPRISE_DATABASE` record for `ACC-9021`.

**State transitions**:

No new state transitions.

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/v1/meta/chat/completions` | POST | model and messages from the Engine | `{status, tenant_id, model_output}` envelope, with the upstream response inside `model_output` | `X-Tenant-API-Key` | 400 guardrail block, 401 or 403 auth, 503 missing inference setup or unavailable service |
| `/v1/tools/execute` | POST | tool name and arguments | tool result envelope | tenant principal with `tools:execute` | 403 missing permission, 503 MCP configuration, 502 MCP failure |

**Value sourcing**:

| Action | Value produced or displayed | Source |
|---|---|---|
| Route the synthetic account request | account record and route name | Enterprise Agent Engine `fetch_enterprise_record` and LangGraph `router_node` |
| Call the model | completion content and model usage when returned | AgentShield `model_output`; the Engine extracts the assistant content and any usage from the upstream response |
| Display execution cost | cost estimate or unavailable | Known model pricing only; unknown pricing remains unavailable and is omitted from `cost_usd` telemetry |
| Display execution latency | elapsed time | Existing Enterprise Agent Engine timer around the model request |
| Record PII redaction | redaction count and model | AgentShield Meta gateway security event |
| Record injection denial | model and warning level | AgentShield Meta gateway security event |
| Record permission denial | required permission and denial | AgentShield RBAC telemetry |
| Verify shipping status | Splunk and file integrity health | AgentShield `/v1/health/services` |

**Key invariants**:

1. The integrated demo model call goes through AgentShield. It never silently falls back to a direct provider or a mock response.
2. The RAG example uses only synthetic records.
3. Prompt contents, account records, and credentials do not appear in Splunk security events.
4. External LangSmith tracing stays disabled for the demo. Dynatrace is not required.
5. The injection example is sent directly to AgentShield because the Engine's local guardrail may block it first.
6. The RAG lookup remains local and is not described as an AgentShield authorized tool call.
7. Only the `VECTORLESS_RAG` model call uses AgentShield in this demo. Other Engine routes are not changed.

**Security model**:

The Enterprise Agent Engine uses a tenant credential accepted by AgentShield. The demo credential is supplied through the local environment and is never committed. AgentShield is responsible for gateway authentication, prompt injection checks, PII redaction, and security telemetry for the model request. The Engine's own guardrail remains enabled. Only synthetic account data may be used.

**Configuration required**:

1. `AGENTSHIELD_BASE_URL`: local gateway base URL for the Engine's demo model client.
2. `AGENTSHIELD_TENANT_API_KEY`: active tenant key sent as `X-Tenant-API-Key`.
3. `BASETEN_API_KEY`: configured only for AgentShield's upstream model call, when live inference is used.
4. `LANGCHAIN_API_KEY`: unset for the demo so prompt inputs are not sent to external LangSmith tracing.
5. The Engine's gateway backed RAG mode disables both direct provider fallback and mock fallback. It does not require the Engine's local Ollama service or fallback provider credentials.
6. AgentShield, Redis, and Splunk are running and healthy before the demo. Live inference uses the configured AgentShield upstream provider.

**Critical test scenarios**:

1. RAG happy path: the Engine retrieves `ACC-9021`, calls AgentShield, reports a completion without provider or mock fallback, extracts content and usage from the response envelope, and does not report unknown model cost as zero, verifies **AC-1**, **AC-2**, **AC-3**, and **AC-10**.
2. Gateway unavailable or credential rejected: the Engine reports a failed gateway request and makes no direct provider request or mock response, verifies **AC-2**.
3. PII redaction: the synthetic contact email is absent from the upstream request and from every event emitted for the request, verifies **AC-4** and **AC-8**.
4. Injection denial: the gateway returns 400, emits the content safe event, and does not call the upstream model, verifies **AC-5**.
5. Tool permission denial: a principal without `tools:execute` receives 403 and the denial event identifies the missing permission, verifies **AC-6**.
6. Demo review: instructions identify synthetic data, the local RAG boundary, health checks, and the absence of Dynatrace tracing, verifies **AC-7**, **AC-8**, and **AC-9**.

## Build plan

1. Add gateway backed model routing for the Enterprise Agent Engine's `VECTORLESS_RAG` route only. Configure its completion client to call AgentShield with the tenant credential and disable direct provider and mock fallback in this mode. Parse the AgentShield response envelope, preserve unknown cost as unavailable, and verify the gateway receives the request and failure does not bypass it, satisfies **AC-1**, **AC-2**, **AC-3**, and **AC-10**.
2. Verify the real local RAG request against the synthetic `ACC-9021` record. Confirm AgentShield redacts the contact email before upstream inference and Splunk receives only the content safe redaction event, satisfies **AC-4**, **AC-8**, and **AC-9**.
3. Keep the direct gateway injection and RBAC examples as separate demo actions. Verify the 400 block, content safe security event, no upstream call, 403 denial, and required permission telemetry, satisfies **AC-5** and **AC-6**.
4. Update the demo instructions with setup, configuration, the RAG query, expected outputs, Splunk searches, and the explicit local tool and Dynatrace boundaries, satisfies **AC-7**, **AC-8**, and **AC-9**.

## Consequences

**Positive**:

1. The demo includes the Enterprise Agent Engine's RAG workflow and makes AgentShield part of its model path.
2. The gateway retains control of model ingress guardrails, PII masking, and its existing Splunk security events.
3. The integration avoids moving the synthetic data store or creating a new tool transport.

**Tradeoffs**:

1. The RAG lookup remains local and is not governed by AgentShield tool authorization.
2. Live completion still depends on AgentShield's inference configuration.
3. The Engine and AgentShield do not yet share a verified Dynatrace trace.

**Out of scope**:

1. A network hosted RAG service or production data connection.
2. Routing the Enterprise Agent Engine's RAG tool through AgentShield.
3. New red team endpoints or a general evaluation aggregation service.
4. Dynatrace instrumentation, dashboards, or trace verification.

## Follow-up

1. Consider exposing the RAG tool through a network transport and authorizing it through AgentShield if the demo needs to prove gateway controlled tool access.
2. Add shared OpenTelemetry trace context and Dynatrace export after the integrated service path is stable. Do not send prompt or account contents as span attributes.
3. Consider a dedicated evaluation result view after the demo establishes which measures need to be retained.

## Verification

This document defines a buildable demo integration. The RAG to AgentShield path and its runtime behavior still require implementation and verification. The earlier documentation only review is not proof that the two applications are integrated.

## References

1. Enterprise Agent Engine `src/orchestration/graph.py`, `src/orchestration/nodes.py`, `src/mcp/server.py`, `src/gateway/litellm_client.py`, and `src/tracing/langsmith_tracker.py`.
2. Enterprise Agent Engine `src/telemetry/dynatrace/tracer.py` and `src/telemetry/dynatrace/otel_collector_config.yaml`.
3. AgentShield `gateway/routers/meta_models.py`, `gateway/auth.py`, `gateway/main.py`, `gateway/telemetry.py`, and `gateway/aggregator.py`.
4. AgentShield Specs 0005, 0012, 0014, and `docs/demo/meta-hackathon-2026.md`.
