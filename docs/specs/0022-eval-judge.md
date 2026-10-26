# 0022. Eval Judge for Quality Scoring

**Date**: 2026-10-26
**Status**: Proposed

## Summary

This feature adds an evaluation judge that automatically scores completed LLM requests for quality. It stamps pass or fail results on request events so the dashboard can show a real quality pass rate.

## Context

The dashboard has a "Quality pass rate" card that currently shows zero because there is no signal feeding it. The metering system only counts quality passes if events have an `eval_passed` flag. We need a judge to evaluate requests and stamp these flags.

## Requirements

**User stories**:
- As a tenant admin, I want to see quality pass rates on the dashboard so I can monitor response quality.
- As a developer, I want automated quality evaluation so I can track model performance.

**Acceptance criteria**:
- **AC-1**: Completed chat requests are automatically evaluated by the judge.
- **AC-2**: Evaluation results are stamped as `eval_passed` (true/false) on the request events.
- **AC-3**: The dashboard quality pass rate card shows real values from judged requests.
- **AC-4**: The judge is measurable, costed, and safe to run on every request.
- **AC-5**: Evaluation results are stored and queryable.

## Options considered

### Option 1: Rule-based judge (Chosen)

Simple heuristics based on response length, presence of required elements, etc.

**Pros**:
- Fast, no external API calls
- No additional cost
- Easy to test
- Safe to run on every request

**Cons**:
- Less sophisticated than LLM judge
- May not catch semantic issues

### Option 2: LLM-based judge

Use another LLM to evaluate responses.

**Pros**:
- More sophisticated evaluation
- Can catch semantic issues

**Cons**:
- Additional cost per evaluation
- Slower
- More complex to implement
- May hit rate limits

## Decision

**Chosen option**: Option 1: Rule-based judge

A simple rule-based judge will be implemented for now. It can be enhanced to use an LLM judge later.

## Rationale

For the hackathon/demo, a rule-based judge is sufficient to populate the dashboard. It's fast, cheap, and safe. We can add LLM-based judging as a follow-up.

## Feature design

**Data model sketch**:
- **Eval Result (Redis Hash)**: `eval:{run_id}`
    - `run_id`: string
    - `tenant_id`: string
    - `eval_passed`: boolean
    - `score`: float (0-1)
    - `reason`: string
    - `timestamp`: float

**API surface**:
No new public endpoints needed. The judge runs internally and stamps events.

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Evaluate request | eval_passed boolean | Rule-based scoring on response content |
| Stamp event | eval_passed flag on request.completed | Added to event data before emission |

**Key invariants**:
- Every completed chat request gets evaluated
- Eval results are tenant-scoped
- Judge never blocks the response

**Security model**:
- Judge runs with system privileges
- No user input used to construct judge prompts that could be exploited

## Critical test scenarios

- Happy path: Chat request completed, judged as passed, dashboard shows quality pass rate, verifies **AC-1**, **AC-2**, **AC-3**
- Failure case: Poor quality response judged as failed, eval_passed is false, verifies **AC-2**
- Edge case: Judge errors don't break the request flow, verifies **AC-4**

## Build plan

1. Implement rule-based judge function in `gateway/evals.py`, satisfies **AC-1**, **AC-4**
2. Integrate judge into `process_llm_request` to evaluate responses, satisfies **AC-1**
3. Stamp `eval_passed` on telemetry events, satisfies **AC-2**
4. Store eval results in Redis, satisfies **AC-5**
5. Update metering to properly count quality passes, satisfies **AC-3**
6. Add tests for the judge, satisfies all ACs

## Consequences

**Positive**:
- Dashboard quality card shows real data
- Provides visibility into response quality
- Foundation for more sophisticated evaluation

**Negative / tradeoffs**:
- Rule-based judge may not be very accurate
- Adds slight latency to request processing

**Neutral**:
- Simple implementation that can be enhanced later

## Follow-up

- [ ] Implement LLM-based judge as an option
- [ ] Add eval configuration per tenant
- [ ] Create eval dashboard page with detailed metrics
