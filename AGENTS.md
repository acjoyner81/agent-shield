# AgentShield Enterprise - Project Context

## Project Overview
AgentShield is a multi-tenant AI gateway and agent platform for small businesses. It focuses on predictable LLM costs, secure tool access, reliable routing, and measurable answer quality.

## Tech Stack
- **Frontend:** Angular (Port 4200)
- **Python Gateway:** FastAPI (Port 8000)
- **Java Gateway:** Spring Boot (Port 8080)
- **MCP Server:** Java/Spring Boot (Port 8081)
- **Infrastructure:** Redis, Splunk, Tripwire, Docker Compose

## Agent Skills & Workflow
We use a structured workflow to ensure technical consistency:

1. `/scope` $\rightarrow$ Define boundaries in `docs/scope/`
2. `/architect` $\rightarrow$ Design decision and write spec in `docs/specs/`
3. `/develop` $\rightarrow$ Implement the feature based on the spec
4. `/check` $\rightarrow$ Verify implementation
5. `/sync` $\rightarrow$ Update this file (AGENTS.md) and project state

### Installed Workflow Skills
- `architect`: Design, decision making, and spec ownership.
- `audit`: Codebase audits and pattern reviews.
- `check`: Implementation verification.
- `debug`: Structured problem solving.
- `develop`: Build guidance and execution.
- `document`: Changelogs and documentation.
- `scope`: Feature scoping and planning.
- `sync`: Context and AGENTS.md management.
- `test`: Testing strategy and writing.

## Current Progress & Decisions
- [x] Basic Docker Compose stack (Redis, Splunk, Tripwire, Gateways, Frontend)
- [x] Splunk platform architecture fix (ARM64 $\rightarrow$ AMD64)
- [x] Tripwire initialization fix (Key generation logic)
- [x] Implemented `/v1/telemetry/logs` (GET/POST) with Splunk HEC integration
- [x] Implemented `/v1/billing/checkout` (POST mock)
- [x] Implemented Granular RBAC Enforcement with Auth0 permissions verification (`require_permission` dependency, Spec 0005)
- [ ] Implement real billing integration (Stripe)
- [ ] Implement real telemetry aggregation from Redis $\rightarrow$ Splunk
- [ ] Build out the Java Gateway core logic

## Project Memory
For quick recall of recent changes, refer to the git history or the `docs/progress.md` (if created).
