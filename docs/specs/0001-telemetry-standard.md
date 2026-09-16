# 0001. Adopt a standardized telemetry and logging pattern for all services

**Date**: 2026-09-14
**Status**: Proposed

## Summary

This decision defines one right way to handle logs and tracing across every service in the platform. It ensures that every request can be followed from start to finish (tracing) and that logs for different customers are kept separate (tenant isolation). By using a fixed format and a shared scrubbing rule, we make our logs safe and easy to analyze in Splunk.

## Context

AgentShield operates as a multi tenant gateway where requests flow through multiple services (Angular Portal, Python Gateway, and Java MCP Server). Currently, there is no unified standard for how these services log events or pass request identities. This leads to fragmented observability where it is hard to link a failure in the MCP server back to a specific user request in the gateway.

To support SOC 2 and GDPR compliance, we must ensure that sensitive data (like auth tokens or emails) never reaches our external log aggregators. Additionally, telemetry must not slow down the primary request path, as the gateway has a strict total overhead budget of 150ms.

## Requirements

**User stories**:
- As a developer, I want to follow a single request across all services using a unique ID so that I can debug distributed failures quickly.
- As a security officer, I want all PII to be masked before logs leave the service so that we comply with data minimization laws.
- As an operator, I want logs to be isolated by tenant ID in Splunk so that I can analyze usage and errors for a single customer.

**Acceptance criteria**:
- **AC-1**: Every log entry follows the defined JSON schema exactly.
- **AC-2**: Services propagate identity using W3C `traceparent` and `X-Tenant-ID` headers.
- **AC-3**: All logs are scrubbed for PII (emails, JWTs, API keys) at the application boundary before serialization using standardized regex masks.
- **AC-4**: Telemetry logic (injection, formatting, and masking) adds less than 5ms of overhead per request.
- **AC-5**: Failures in Redis or Splunk transport result in an async, non-blocking dump of the JSON log to `stdout` using NDJSON format.
- **AC-6**: Gateway generates a new W3C `traceparent` if the incoming header is missing or invalid.
- **AC-7**: Missing `X-Tenant-ID` headers default to "unknown" and trigger a `WARN` level log entry.

## Options considered

### Option 1: Custom simple headers and basic logging
Use a simple `X-Trace-ID` header and a basic shared logging library.

**Pros**:
- Very easy to implement and understand.

**Cons**:
- Requires a breaking header migration when moving to industry standard tracing tools (like OpenTelemetry).

### Option 2: Full OpenTelemetry (OTel) SDK integration
Deploy full OTel SDKs in every service to handle spans, traces, and metrics.

**Pros**:
- Industry standard with massive ecosystem support.

**Cons**:
- Higher implementation complexity and resource overhead for a small MVP.

### Option 3: W3C Trace Context + Custom Tenant Header + Regex Middleware
Use the `traceparent` standard for tracing, a dedicated `X-Tenant-ID` for isolation, and centralized regex middleware for scrubbing.

**Pros**:
- Future proof (compatible with OTel) but lightweight to start.
- Strong, centralized security enforcement via middleware.

**Cons**:
- Requires maintaining a regex list for PII scrubbing.

## Decision

**Chosen option**: Option 3: W3C Trace Context + Custom Tenant Header + Regex Middleware

We will adopt the W3C `traceparent` standard for distributed tracing and use a dedicated `X-Tenant-ID` header for tenant isolation. PII scrubbing will be enforced via centralized middleware in both FastAPI and Spring Boot.

## Rationale

This approach balances immediate simplicity with long term scaling. Using `traceparent` prevents the "breaking change" trap of custom IDs while avoiding the weight of a full SDK. The middleware approach for PII scrubbing is the most reliable way to ensure no sensitive data leaks, as it catches all outgoing logs regardless of who wrote the log statement.

## Standard definition

**Canonical pattern**:

Python (FastAPI):
```python
# Middleware handles traceparent and X-Tenant-ID extraction
# Root Generation: if not request.headers.get("traceparent"): generate_w3c_id()
# Fallback Tenant: tenant_id = request.headers.get("X-Tenant-ID", "unknown")
# Scrubbing applied at the logger level or via middleware
log_entry = {
    "timestamp": datetime.utcnow().isoformat(),
    "trace_id": request.state.trace_id,
    "tenant_id": tenant_id,
    "service_name": "gateway-python",
    "log_level": "INFO" if tenant_id != "unknown" else "WARN",
    "category": "MCP_TOOL",
    "message": scrub_pii("User executed tool"),
    "context": scrub_pii_dict(context_data)
}
# Async ship to Redis/Splunk with NDJSON stdout fallback
telemetry.ship(log_entry)
```

Java (Spring Boot):
```java
// OncePerRequestFilter extracts traceparent and X-Tenant-ID
// Root Generation: if (traceParent == null) { generateW3cId(); }
// Fallback Tenant: String tenantId = header != null ? header : "unknown";
MDC.put("trace_id", traceId);
MDC.put("tenant_id", tenantId);
log.info("Executed MCP tool successfully", StructuredArgument.kv("category", "MCP_TOOL"));
```

**PII Regex Masks**:
All matches are replaced with `[MASKED]`.
- **JWT**: `eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*`
- **API Keys**: `(sk|pk)_(live|test)_[0-9a-zA-Z]{24,}`
- **Emails**: `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}`

**Replaces**:
- Inconsistent log formats across services.
- Custom `X-Trace-ID` headers.
- Manual, per-statement PII masking.

**Enforcement**:
Centralized middleware: `BaseHTTPMiddleware` in FastAPI and `OncePerRequestFilter` in Spring Boot.

**Rollout**:
Enforce immediately for all new code. Existing logs will be migrated as part of the "End to End Flow" feature.

**Exceptions**:
None, no exceptions.

**Transport Format**:
All `stdout` emissions must use **NDJSON (Newline Delimited JSON)**.

## Consequences

**Positive**:
- Uniform observability across the entire request chain.
- Guaranteed tenant isolation in Splunk.
- Reduced risk of PII leaks (SOC 2 readiness).

**Negative / tradeoffs**:
- Slight CPU overhead from regex scanning on every log entry.

**Neutral**:
- Developers must use the standardized `category` field for all logs.
