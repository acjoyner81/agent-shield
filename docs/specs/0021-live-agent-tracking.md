# 0021. Live Agent Runtime and Evaluations Tracking

**Date**: 2026-10-25
**Status**: Proposed

## Summary

This feature adds live tracking for agent executions and evaluation results. It allows users to see active runs and recent quality scores in the portal in real time. The system uses Redis to group related events into a single run and polls these runs for the UI.

## Context

The current Agent Runtime and Evaluations pages are static mockups. Users cannot see if an agent is currently working or if a recent request was judged as high quality. Without this, the "Agent Runtime" is just a placeholder and the "Evaluations" page has no data to display.

## Requirements

**User stories**:
- As a tenant admin, I want to see currently active agent executions so that I can monitor long running workflows.
- As a developer, I want to see recent evaluation scores for my prompts so that I can iterate on quality.

**Acceptance criteria**:
- **AC-1**: The portal displays a list of "Active Executions" fetched from the gateway.
- **AC-2**: A "run" is defined as a group of events sharing a `run_id` (the initial request plus any subsequent tool calls).
- **AC-3**: The gateway provides an endpoint `GET /v1/agent/executions` that returns currently active runs for the authenticated tenant.
- **AC-4**: The gateway provides an endpoint `GET /v1/evals/recent` that returns recently judged executions and their scores.
- **AC-5**: A subset of completed runs is automatically dispatched to the Eval Judge (Feature 16) for scoring.
- **AC-6**: The portal polls these endpoints every 5 seconds to update the UI without page refreshes.

## Options considered

### Option 1: Polling (Chosen)

The portal polls the gateway at a fixed interval.

**Pros**:
- Simple to implement
- Consistent with existing Dashboard patterns
- Easy to debug via network tab

**Cons**:
- Slight delay in updates
- Unnecessary requests when the page is idle

### Option 2: WebSockets / SSE

The gateway pushes updates to the portal.

**Pros**:
- True real time updates
- Reduced request overhead

**Cons**:
- Higher architectural complexity (requires stateful connections)
- Potential issues with the Nginx proxy configuration

## Decision

**Chosen option**: Option 1: Polling

The system will use a polling mechanism to fetch active runs and evaluation results.

**Implementation skills**: `develop` (`.agents/skills/develop/`)

## Rationale

Polling is chosen for its simplicity and alignment with the existing portal architecture. Given the expected frequency of agent runs, a 5 second poll is sufficient for the "live" feel required for the hackathon demo without introducing the complexity of a streaming infrastructure.

## Feature design

**Data model sketch**:
- **Run Store (Redis Hash)**: `run:{run_id}`
    - `tenant_id`: string
    - `status`: string (active | completed | failed)
    - `start_time`: float
    - `model`: string
    - `prompt`: string
- **Active Runs Index (Redis Set)**: `active_runs:{tenant_id}` (stores `run_id`s)
- **Evaluation Store (Redis List)**: `evals:{tenant_id}` (stores JSON objects with `run_id`, `score`, `reason`, `timestamp`)

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /v1/agent/executions | GET | none | list of {run_id, status, model, startTime} | bearer/key | 401, 403 |
| /v1/evals/recent | GET | none | list of {run_id, score, reason, timestamp} | bearer/key | 401, 403 |

**Value sourcing**:

| Action | Value produced / displayed | Source |
|---|---|---|
| Fetch Active Runs | List of executions | `active_runs:{tenant_id}` set $\rightarrow$ `run:{run_id}` hashes |
| Fetch Recent Evals | List of scores | `evals:{tenant_id}` list |
| Trigger Eval | Judge Result | Evaluated via Eval Judge (Feature 16) |

**Key invariants**:
- A `run_id` must be created at the start of every `/v1/chat/completions` request.
- The `run_id` must be propagated to any tool calls triggered by that request.
- Active runs must be removed from the `active_runs` index upon completion or failure.

**Security model**:
- Users can only see runs and evaluations belonging to their own `tenant_id`.

## Critical test scenarios

- Happy path: Trigger a chat request, see it appear in "Active executions", and see it eventually appear in "Recent Evals", verifies **AC-1**, **AC-2**, **AC-3**, **AC-4**, **AC-5**, **AC-6**
- Failure case: A run crashes or times out, it is removed from the active list, verifies **AC-2**
- Auth/permission: Tenant A cannot see runs for Tenant B, verifies **AC-3**, **AC-4**

## Build plan

1. Implement `run_id` generation and tracking in `gateway/main.py` (create `run:{run_id}` and add to `active_runs` index), satisfies **AC-2**
2. Implement `GET /v1/agent/executions` endpoint to return active runs, satisfies **AC-3**
3. Implement `GET /v1/evals/recent` endpoint to return judged runs, satisfies **AC-4**
4. Implement automatic dispatch logic to trigger Eval Judge for completed runs, satisfies **AC-5**
5. Update `AgentRuntimeComponent` to poll `/v1/agent/executions` and render the list, satisfies **AC-1**, **AC-6**
6. Update Evaluations page to poll `/v1/evals/recent` and render the results, satisfies **AC-1**, **AC-6**

## Consequences

**Positive**:
- Users get real time feedback on agent activity.
- Quality scoring becomes visible and actionable.

**Negative / tradeoffs**:
- Slightly increased Redis load due to run tracking and polling.

**Neutral**:
- Introduces a new `run_id` concept to the telemetry flow.

## Follow-up

- [ ] Implement a "Stop Run" endpoint to manually terminate active executions.
- [ ] Add detailed step-by-step trace view for a specific `run_id`.
