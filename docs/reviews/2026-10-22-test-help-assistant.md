# Review, uncommitted, 2026-10-22

**Reviewed by**: big-pickle (author on big-pickle)  
**Scope**: 2 files changed (gateway/tests/test_help_assistant.py new, docs/scope/scope.md small edits), uncommitted  
**Verdict**: Approve with nits

## Summary

Adds comprehensive integration tests for the help assistant endpoint (Spec 0017) with deterministic corpus, Baseten stubbing, and coverage of guardrails/auth/provider failures/usage gating. The tests follow repo conventions (AC references in docstrings) and exercise the app through TestClient with real auth/redis boundaries controlled by fixtures. Overall strong - exercises contract and error paths well. A few areas where assertions are indirect or could be tightened to catch implementation details that matter (e.g., ensuring retry behavior, citation shape, and some false-green risks). The scope doc updates are cleanly aligned with the verify/test runs.

## Major

### 🟠 Upstream error class coverage conflates `RequestError`/`TimeoutException` mapping - `gateway/tests/test_help_assistant.py:335-346`
**Problem**: `test_provider_timeout_returns_retryable_503` stubs `upstream.error = httpx.TimeoutException("slow")` and expects 503 with `Retry-After: "5"`. The fixture replaces `help_assistant.httpx` with a namespace exposing `TimeoutException` but the test relies on the endpoint's exception handling to map `httpx.TimeoutException`/`RequestError` to 503 with retry. The repo states: "timeout/RequestError -> 503 with Retry-After" (from placeholder). The test only covers `TimeoutException`. A regression that only catches `RequestError` (or vice versa) could slip if not tested.  
**Why it matters**: The AC-9 requires retryable 503 for timeouts/RequestError; missing coverage for `RequestError` leaves that branch unverified.  
**Suggested fix**: Add a test that sets `upstream.error = httpx.RequestError("network")` (via the stubbed namespace) and asserts 503 with `Retry-After` header, matching the spec's contract.

### 🟠 `test_provider_status_error_returns_safe_502` only checks detail substring - `gateway/tests/test_help_assistant.py:347-357`
**Problem**: Asserts status 502 and `"rejected"` in `response.json()["detail"]`. It doesn't verify that the upstream response body/status is not leaked (the test name says "content safe 502"). Also uses `FakeErrorResponse(500)` which will raise on `raise_for_status()` - the test relies on the endpoint catching `HTTPStatusError`. That's fine, but the assertion is weak on safety.  
**Why it matters**: "Content safe" means no upstream internals leaked; a detail like `"rejected"` might be fine, but the test should be explicit about not leaking status text or raw error bodies.  
**Suggested fix**: Assert the detail does not contain upstream status code strings/raw bodies and is a stable, safe message. Also consider asserting no sensitive fields are present.

### 🟠 `test_provider_unparseable_body_returns_502` weak on "invalid JSON" - `gateway/tests/test_help_assistant.py:359-369`
**Problem**: Uses `FakeErrorResponse(200, json_error=ValueError("bad json"))` so `raise_for_status()` won't raise (200). The endpoint must handle JSON parse failure on a 200 response. The test asserts `"invalid JSON"` in detail - again substring check. If the endpoint logs details but returns safe message, detail content matters.  
**Why it matters**: Unparseable envelope must fail closed to safe 502 (AC-9). The test verifies behavior but assertion is loose.  
**Suggested fix**: Strengthen to assert detail is a generic safe string (or matches expected shape) and doesn't leak exception text.

## Minor

### 🟡 History shape edge case partially covered but "must end with assistant" test relies on schema - `gateway/tests/test_help_assistant.py:323`
**Problem**: Parametrized case `{"question": "hi", "history": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}] * 5}` expects 422 with id `"too-many"`. The test name/ids suggest count limit; the comment says "too-many" but the case is 10 turns (alternating). Is this testing max history length or alternation? The schema likely enforces both; the test conflates them in id.  
**Why it matters**: Test intent is unclear - if limit is the thing being tested, better to make it explicit. Also good that it's covered.  
**Suggested fix**: Split or clarify - add a comment explaining which constraint is hit, or use a clearer id. Not blocking.

### 🟡 `test_unsupported_question_abstains_without_calling_provider` doesn't assert citation shape beyond URL - `gateway/tests/test_help_assistant.py:207-220`
**Problem**: Asserts status 200, citations[0].url points to troubleshooting, usage is all-null, upstream.calls empty. Doesn't assert source_id/title/paragraph text shape for abstention. The abstention behavior is specified (AC-3) - should verify it returns a proper abstention response structure.  
**Why it matters**: Contract completeness - other tests check citations shape; abstention path should too.  
**Suggested fix**: Assert citations contain expected abstention source_id/title and paragraph text indicates verification/help center link.

### 🟡 `test_prompt_injection_in_history_is_blocked` only checks status and no dispatch - `gateway/tests/test_help_assistant.py:254-270`
**Problem**: Verifies 400 and no upstream call when injection appears in history. Doesn't assert the response detail mentions guardrails (like the question case does).  
**Why it matters**: Consistency of error reporting; if detail differs, clients behave differently.  
**Suggested fix**: Assert `"guardrails" in response.json()["detail"]` like the question injection test.

### 🟡 PII redaction test checks outbound and inbound but doesn't verify no leakage in events - `gateway/tests/test_help_assistant.py:272-289`
**Problem**: `test_pii_is_redacted_before_dispatch_and_in_returned_text` verifies redaction in sent messages and response text. AC-8 says "no prompt text... retained or logged" - there's a separate test `test_prompt_text_is_absent_from_emitted_events` that checks markers aren't in captured events. PII is related to privacy; maybe worth asserting PII marker also not in events? The corpus is synthetic so no PII, but the redaction is happening. Not strictly required if covered elsewhere.  
**Why it matters**: Defense in depth - good to ensure redaction doesn't just transform for display but also doesn't leak in telemetry.  
**Suggested fix**: Optionally extend event-capture test or add assertion here.

### 🟡 `test_unparseable_model_message_abstains` - good behavior, could assert no error logged as 502 - `gateway/tests/test_help_assistant.py:371-382`
**Problem**: Test correctly expects abstention (200) when model returns unparseable JSON content. This is important (fail-soft vs fail-hard). Assertion is reasonable.  
**Why it matters**: Correct behavior per AC-7/"unparseable model message abstains" - good test.  
**Suggested fix**: None critical; could also assert usage is null like unsupported question.

### 🟡 Cost gating tests use `patch.object` extensively - httpx stubbing sound overall - `gateway/tests/test_help_assistant.py:396-484`
**Problem**: Tests patch `emit_token_usage`, `get_prices`, `estimate_cost_usd` etc. The stubbing of httpx via fixture is clean - replaces the module's httpx with controlled FakeAsyncClient. State is captured in `upstream` fixture. This properly exercises app control flow. One thing: when cost is computed, `test_cost_requires_billing_permission_and_a_verified_rate` patches `estimate_cost_usd` to return fixed value and also patches `get_prices` - the endpoint likely calls both; test verifies cost appears. Good.  
**Why it matters**: httpx stubbing is sound and doesn't hit network.  
**Suggested fix**: Minor - consider verifying `emit_token_usage` called with correct tenant/model when costs computed.

## Nits

- ⚪ `gateway/tests/test_help_assistant.py:7` - Docstring says "reads the developer's corpus file" but tests stub CORPUS_PATH to synthetic corpus; wording could be clearer.
- ⚪ `gateway/tests/test_help_assistant.py:27` - Module-level TestClient created at import time; with fixtures patching module attributes it's fine, but could be created in fixture to avoid import-order issues. Repo pattern varies.
- ⚪ `gateway/tests/test_help_assistant.py:72-81` - `dev_tenant` fixture patches `gateway.rate_limit.check_token_bucket` and sets `baseten_api_key`. Good isolation. Also patches `CORPUS_PATH` on the router module - correct.
- ⚪ `gateway/tests/test_help_assistant.py:110-160` - `upstream` fixture is well-structured; replaces httpx module cleanly. Good.
- ⚪ `gateway/tests/test_help_assistant.py:163-169` - `_as_billing_admin()` overrides dependency properly; remember to clear overrides if tests run in different order? TestClient app is shared; overriding verify_jwt is fine as long as not leaked - but tests that use it don't clear. Might affect subsequent tests if not careful. Check: `app.dependency_overrides` persists. Better to use `yield`/cleanup or set/reset.
- ⚪ `gateway/tests/test_help_assistant.py:191-205` - Good test for citation validation fallback to abstention.
- ⚪ `gateway/tests/test_help_assistant.py:237` - `test_model_supplied_markup_is_returned_as_plain_text` checks reasoning stripped by ensuring "reasoning" not in json.dumps(body) - reasonable defensive check.
- ⚪ `gateway/tests/test_help_assistant.py:334` - Parametrized test has good ids; schema validation coverage is solid.
- ⚪ `gateway/tests/test_help_assistant.py:509` - `test_prompt_text_is_absent_from_emitted_events` is a strong privacy check.
- ⚪ `docs/scope/scope.md:274, 280-281` - Scope updates correctly mark build and test as done with verification notes matching the run. Clean.

## Strengths

- **Deterministic isolation**: Synthetic corpus fixture + Baseten httpx stub + fakeredis via dev_mode/fake_redis prevents external calls and host Redis usage. Matches project constraints.
- **Contract coverage**: Exercises auth (machine key rejected, missing creds), schema (comprehensive invalid shapes), guardrails (injection in question/history, PII redaction both ways), provider errors (timeout/503, 502s, unparseable), retrieval/citations/abstention, and usage/cost gating with permission checks.
- **Behavior-focused tests**: Many tests verify dispatch behavior (upstream.calls == []), not just return codes. Good.
- **Follows repo style**: Docstrings reference ACs, uses TestClient, patches modules appropriately.
- **Privacy-conscious**: Explicit test ensuring prompt/answer text not in emitted events.

## Test coverage

**Covered well**: AC-3 (retrieval, citation validation, abstention paths), AC-6/AC-9 (auth, schema), AC-7 (injection, PII, markup safety), AC-8 (usage emission, cost gating). Provider error mapping covered for timeout, HTTPStatusError, unparseable envelope, unset creds. History validation comprehensive.

**Gaps/minor concerns**:
- `RequestError` (non-timeout network errors) not explicitly tested - only `TimeoutException` tested. Endpoint states both map to 503 with Retry-After; add coverage.
- `HTTPStatusError` detail safety could be asserted more strictly (no leakage). 
- Abstention response shape not fully asserted in all abstention cases.
- Dependency override in `_as_billing_admin()` not cleaned up - could leak between tests if order changes (mitigated by how tests use it, but better practice).
- `test_cost_requires_billing_permission_and_a_verified_rate` doesn't verify `emit_token_usage` called with expected args; other usage tests do.
- No explicit test for the case where model returns valid JSON but paragraphs cite valid sources mixed with invalid? `test_unknown_citation_ids_fall_back_to_abstention` replaces all with unknown - good. What if some valid some invalid? The implementation likely filters/abstains if any invalid; test covers the all-invalid case.

Overall coverage is strong for a new test suite. The httpx stubbing is sound and exercises control flow.

REVIEWED_BY: big-pickle
SCOPE: 2 files, uncommitted
FINDINGS_FILE: /Users/anthonyjoyner/Documents/Projects/agent-shield/docs/reviews/2026-10-22-test-help-assistant.md
VERDICT: Approve with nits

BLOCKERS:
- (none)

MAJOR:
- gateway/tests/test_help_assistant.py:335-346, Missing coverage for httpx.RequestError mapping to 503 with Retry-After (only TimeoutException tested)
- gateway/tests/test_help_assistant.py:347-357, Weak assertion on content-safe 502 detail (doesn't verify no upstream leakage)
- gateway/tests/test_help_assistant.py:359-369, Weak assertion on safe 502 detail for unparseable envelope

MINOR_COUNT: 7
NIT_COUNT: 11

STRENGTHS: Deterministic isolation (synthetic corpus + httpx stub + fakeredis), comprehensive contract/error/guardrail coverage, behavior-focused assertions (dispatch checks), strong privacy test ensuring no prompt text in events
