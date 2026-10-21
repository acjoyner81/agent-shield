# 0016. Portal Help Center

**Date**: 2026-10-20
**Status**: Proposed

## Summary

Replace the placeholder at `/docs` with a public, one page guide for tenant admins and developers. It will follow the portal’s current design and explain the portal, API keys, a first Baseten Meta request, usage and health, and common errors. The guide will contain no tenant specific information.

## Context

The current Help Center route is public, but its page contains only “docs works!”. Tenants have no in product path to understand the portal or make a first request safely. The API Keys page already has a short request example, and the gateway has a dedicated Meta route backed by Baseten.

The guide must distinguish the live Baseten Meta route from the generic chat route, which currently returns a mock response. Baseten inference also depends on a provider credential configured on the gateway. The guide must make that setup requirement clear without exposing a secret or implying that a tenant key configures the provider.

## Requirements

**User stories**:
- As a tenant admin, I want to understand the portal and manage a tenant API key so that I can safely grant gateway access.
- As a developer, I want a verified key flow and a working model request example so that I can make my first inference request.
- As a tenant admin or developer, I want to understand usage, health, and common failures so that I can diagnose basic issues.

**Acceptance criteria**:
- **AC-1**: `/docs` presents a single page in the existing portal visual style with section links and shortcuts to relevant portal pages.
- **AC-2**: Sections appear in this order: portal overview, API key, first request, usage and health, troubleshooting.
- **AC-3**: API key guidance explains that the key is created in API keys & access, shown only once, sent in `X-Tenant-API-Key`, and must not be placed in source control or shared publicly.
- **AC-4**: The first request guide provides a key verification example followed by a Baseten Meta request example using a configured model placeholder or the configured default, and explains that the gateway operator must set `BASETEN_API_KEY` and may set `META_API_BASE_URL`.
- **AC-5**: The examples use placeholders only, do not contain real credentials, and identify the Meta endpoint as `POST /v1/meta/chat/completions`.
- **AC-6**: Usage and health guidance links to the portal’s observability view and explains that displayed service health is shared operational status, not tenant specific data.
- **AC-7**: Troubleshooting covers invalid or missing keys, insufficient permissions for protected portal or key management actions, and degraded services with a clear next action and a relevant portal shortcut for each. It does not imply that Meta inference requires a separate permission scope.
- **AC-8**: The page remains accessible without signing in and renders only static, public guidance. The Help Center content makes no tenant data requests and displays no tenant credentials, usage, health details, or other tenant data. The existing shared app shell may continue to show shared operational service status.
- **AC-9**: The page is usable at narrow viewport widths and its section navigation and portal shortcuts are keyboard accessible.

## Options considered

### Option 1: One static in portal guide (Recommended)

Build the existing route as a static Angular page with anchored sections, code examples, and links to the portal’s existing pages.

**Pros**:
- Keeps the guidance close to the product and requires no new backend surface.
- Preserves public access without fetching tenant data.
- Fits the requested topic set and one page navigation.

**Cons**:
- Content changes require a frontend release.
- It does not provide searchable, versioned API reference material by itself.

### Option 2: Link out to existing API reference only

Keep the Help Center brief and send developers to the gateway OpenAPI page for all request guidance.

**Pros**:
- Avoids duplicating endpoint schemas.
- The API reference is generated from the gateway contract.

**Cons**:
- Does not answer the portal onboarding and troubleshooting needs.
- The Meta route is currently excluded from the OpenAPI schema.

### Option 3: Fetch live service and tenant information in the guide

Turn the Help Center into a dashboard that loads account and operational data.

**Pros**:
- Could show context specific status and usage.

**Cons**:
- Conflicts with the requirement to keep the page public and free of tenant data.
- Adds authentication, loading, failure, and data freshness behavior to a documentation page.

## Decision

**Chosen option**: Option 1: One static in portal guide

Create a public Angular guide at the existing `/docs` route, using anchored sections and shortcuts in the current portal design. Show the configured Baseten Meta route and required gateway setup, without presenting the generic mock chat route as live inference.

## Rationale

The existing route, portal design, and gateway already provide the needed foundation. Static content satisfies onboarding without introducing a new API or exposing tenant information. A direct Baseten example is useful, provided the guide distinguishes the gateway’s provider secret from the tenant’s API key and labels configuration as an operator prerequisite.

## Feature design

**Data model sketch**:
None. The page is static frontend content and does not persist data.

**API surface**:
| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/docs` | GET | None | Static Help Center page | Public | Not applicable |
| `/v1/usage/summary` | GET | `X-Tenant-API-Key` header | Authenticated tenant’s usage summary; cost fields require `billing:admin` | Tenant key or accepted bearer token | 401 missing or invalid credentials, 422 invalid date parameters |
| `/v1/meta/chat/completions` | POST | `X-Tenant-API-Key`; JSON `model` and `messages` | Meta model completion through Baseten | Tenant key or accepted bearer token; no separate permission scope check | 400 guardrail rejection, 401 invalid credentials, 503 missing provider configuration or upstream unavailable |

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Render overview and topic content | Portal feature descriptions and section order | Confirmed Help Center requirements in this spec |
| Show portal shortcuts | Route paths for API keys, observability, models and API reference | Existing Angular routes and frontend gateway proxy configuration |
| Show key instructions | Header name and one time display behavior | Existing gateway auth and API Keys page behavior |
| Show key verification example | Gateway URL and usage summary path | Current portal origin plus the gateway’s `/v1/usage/summary` route; user supplies their own key as an environment variable |
| Show Meta inference example | Gateway URL, `POST /v1/meta/chat/completions`, model and message shape | Existing `gateway/routers/meta_models.py`; model placeholder resolves to configured default in `agent_shield_config.yaml` |
| Explain provider setup | `BASETEN_API_KEY` and optional `META_API_BASE_URL` | `config/settings.py` and `agent_shield_config.yaml`; values are set by the gateway operator and never rendered into the page |
| Explain health status | Shared service health concept and observability shortcut | Existing portal observability and health UI; `/docs` content does not fetch tenant data, while the shared shell may fetch service health |
| Show troubleshooting actions | Destination pages and user actions, including where scope requirements apply | Existing portal routes, key management permissions, and gateway auth behavior; Meta inference uses tenant authentication without an additional permission scope |

**Key invariants**:
- Help content is public and static, and the page content contains no tenant specific values or tenant data requests. The shared shell may display shared service health.
- No real API key, provider key, token, or user supplied content is embedded in examples.
- Tenant API credentials authenticate the caller to AgentShield. `BASETEN_API_KEY` authenticates the gateway to Baseten. The two credentials are not interchangeable.
- The Meta example points to `/v1/meta/chat/completions`; it does not describe `/v1/chat/completions` as live Baseten inference.
- If the page cannot link to or verify a function, it must not claim that function works.

**Security model**:
Anyone may read `/docs`. The Help Center content makes no authenticated API calls and displays no tenant data. The shared shell remains responsible for its existing shared service health status. Examples use shell variables such as `$AGENTSHIELD_API_KEY` and instruct readers to keep secrets out of source control. The gateway continues to enforce tenant authentication and guardrails when the sample API calls are made.

**Configuration required**:
- `BASETEN_API_KEY`: gateway operator credential required for Baseten inference; never shown to tenants.
- `META_API_BASE_URL`: optional gateway operator override for the Baseten compatible API base URL.

**Critical test scenarios**:
- Happy path: open `/docs` without a session and navigate through all five sections and portal shortcuts, verifies **AC-1**, **AC-2**, **AC-8**, **AC-9**.
- Failure case: review the key verification and Meta examples; they use placeholders, name correct route and header, distinguish provider setup errors, and do not claim a separate Meta permission scope, verifies **AC-3**, **AC-4**, **AC-5**, **AC-7**.
- Auth and permission: the Help Center itself loads publicly; guidance distinguishes authentication failures from permission failures on protected portal actions, verifies **AC-7**, **AC-8**.
- Health guidance: confirm the page links to observability, makes no tenant data request, and permits the shared shell’s operational status, verifies **AC-6**, **AC-8**.

## Build plan

1. Replace the placeholder page with the one page overview and ordered section navigation in the existing portal style, satisfying **AC-1**, **AC-2**, **AC-9**.
2. Add API key guidance and safe key verification plus Baseten Meta request examples, satisfying **AC-3**, **AC-4**, **AC-5**.
3. Add usage, shared health, and troubleshooting guidance with links to existing portal destinations, satisfying **AC-6**, **AC-7**.
4. Confirm the route remains public and contains no live tenant data, satisfying **AC-8**.

## Consequences

**Positive**:
- Tenant admins and developers get a practical starting point in the portal.
- The guide sets correct expectations about Meta model setup, API credentials, and the mock generic chat endpoint.
- Public access does not require a session or expose tenant information.

**Negative / tradeoffs**:
- Maintainers must update the examples when route or configuration names change.
- The page duplicates a small amount of API usage guidance and can become stale without review.

**Neutral**:
- No database, backend endpoint, or new runtime dependency is required.
- The Meta endpoint remains excluded from generated OpenAPI unless that is changed separately.
