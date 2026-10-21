# 0017. Build a documentation assistant

**Date**: 2026-10-20
**Status**: In Progress

## Summary

The portal will offer a signed in assistant that answers questions from approved AgentShield documentation. The public Help Center and the assistant will use the same curated Markdown, so every citation points to content users can read. The assistant will use the configured Meta model through the gateway, keep conversation history in browser memory, and avoid storing or logging prompt text.

## Context

Tenant administrators and developers need help using portal features and making their first API request. A documentation assistant can guide them without taking actions on their account or exposing tenant data. The existing portal is Angular, and the gateway already provides tenant authentication, model routing, guardrails, telemetry, and usage metering.

The assistant must answer only from verified public documentation. The supplied HTML is a visual reference, not a source of behavior or content. Its fabricated service status and model responses, embedded credential, and unsafe HTML rendering are not part of this design.

## Requirements

**User stories**:
- As a signed in tenant administrator or developer, I want cited answers to questions about AgentShield documentation so that I can use the portal and API confidently.
- As a tenant user, I want to retry a failed question or clear the conversation so that I can recover without retaining chat history on the server.

**Acceptance criteria**:
- **AC-1**: A signed in tenant user can open `/assistant` from the sidebar and public Help Center. The page follows the current portal design, displays citations directly with their answer paragraphs, and remains usable at 375 CSS pixels and wider without horizontal page scrolling.
- **AC-2**: The public Help Center remains accessible without sign in and displays no tenant data. Its content and assistant retrieval corpus come from the same curated Markdown pages in `docs/help-center`.
- **AC-3**: Retrieval searches only the approved corpus. A supported answer is returned as paragraphs, each tied to at least one retrieved source ID supplied by the server. The server rejects unknown citation IDs and abstains if retrieval finds no source above the documented threshold. The fixed abstention links to `/docs` and suggests asking the workspace owner; it does not claim that an unmatched page is relevant. Citation validation provides evidence for review but does not prove every model claim is entailed by its citation.
- **AC-4**: The page starts with a few documentation question suggestions and provides a cited message thread, a composer, a clear action, and a visible loading state while a complete answer is generated. It does not stream output.
- **AC-5**: The browser retains the current conversation only in memory until the page closes or the user clears it. A request contains a nonblank question of at most 4,000 Unicode code points and at most eight prior message objects, each with a nonblank `content` string of at most 4,000 code points and role `user` or `assistant`; total history is at most 8,000 code points. The message order alternates, citations are not sent back as history, and unknown fields are rejected. Failed requests keep the draft question and provide an inline error with a manual retry action. The UI prevents duplicate submission, retries the same preserved question, honors `Retry-After` for 429, and ignores late responses after clear or navigation.
- **AC-6**: The authenticated `POST /v1/help/assistant` endpoint accepts the bounded schema, derives tenant and user from `verify_jwt`, rejects machine API keys, and requires no additional permission scope. It returns answer paragraphs with source IDs, server selected citations, and nullable request usage. It is an internal portal operation and is excluded from public `/openapi.json`. It accepts no caller supplied model, source corpus, tenant ID, or provider credential. Pydantic rejects unknown fields; semantic request errors use 422 with the gateway's standard `detail` envelope.
- **AC-7**: The endpoint uses `settings.meta_default_model`, `settings.meta_request_timeout_seconds`, and `settings.meta_max_tokens_limit`, does not stream or automatically retry provider requests, and uses a fixed low temperature of 0.2. It applies the existing prompt injection scan to the current question and every history text before dispatch, then applies configured PII redaction before sending text to the provider and to model output before returning it. It applies the existing reasoning field sanitizer before interpreting provider output. Blocked prompts return 400 and never reach the provider. Provider errors return a content safe 503 or 502. The model returns structured paragraphs, each with one or more retrieved source IDs; the server validates IDs and replaces malformed, unknown, incomplete, or uncited output with the fixed abstention. Output is bounded by the configured token limit and 8,000 Unicode code points; control characters other than line breaks and tabs are removed. Angular renders paragraph text through interpolation and model supplied HTML, links, or markup are never rendered.
- **AC-8**: Prompt text and conversation history are not persisted or included in logs or telemetry, including exception logs. The existing request completed event records method, route, status, latency, tenant, user, and trace; assistant and guardrail audit events may add only model, outcome, status, and redaction counts. A token usage event is emitted only when provider `usage.prompt_tokens` and `usage.completion_tokens` are both present and valid; `usage.total_tokens` is used when valid, otherwise total is their exact sum. If either component is missing or invalid, no token event is emitted and response token counts and cost are null. No token count is estimated. Each authenticated request attempt is recorded by normal tenant scoped request telemetry even when provider usage is absent. Request usage returns available token values, and cost is null unless the provider counts are complete, the configured model has a verified per 1,000 token rate in `gateway/config/prices.json`, and the principal has `billing:admin`. Cost uses the existing `estimate_cost_usd` rule. Missing or unverified rates never appear as zero cost.
- **AC-9**: Missing or invalid user credentials receive 401; a verified token without a tenant claim receives 403. Invalid, blank, oversized, or malformed input receives 422. Prompt injection rejection receives 400. Tenant rate limiting remains enforced by the existing app dependency and returns 429 with `Retry-After`. Provider timeout returns retryable 503, invalid provider JSON returns retryable 502, and other provider rejection returns a safe nonretryable 502. The UI keeps the question for retryable failures and does not retry automatically.

## Options considered

### Option 1: Angular page with a gateway endpoint and curated Markdown

Build the assistant into the existing portal and use a small deterministic retrieval index over the approved Markdown corpus.

**Pros**:
- Reuses portal auth, design, gateway guardrails, and metering.
- Keeps source retrieval transparent and citations stable without another service.
- The public Help Center and assistant share one source of truth.

**Cons**:
- The team must maintain a small retrieval and Markdown rendering path.
- Model answers can still be wrong, so retrieval, citations, and abstention must be enforced server side.
- Citation validation cannot establish semantic entailment. The page must not promise that citations are a formal proof of every generated claim.

### Option 2: Separate Streamlit application

Build a standalone chat app using a Python UI framework.

**Pros**:
- Provides chat controls quickly for a prototype.

**Cons**:
- Adds a second UI, authentication integration, deployment surface, and visual system.
- Does not naturally reuse the existing portal's tenant session or navigation.

### Option 3: Add a chat or retrieval library

Use a new client chat library or a hosted/vector retrieval component.

**Pros**:
- Could reduce custom work for a larger or more interactive assistant.

**Cons**:
- Adds dependencies and behavior that this small, curated documentation corpus does not need.
- A hosted or vector service adds operational and privacy complexity without a demonstrated retrieval need.

## Decision

**Chosen option**: Option 1: Angular page with a gateway endpoint and curated Markdown.

Build a signed in documentation assistant in the existing Angular portal. Store approved Help Center material as curated Markdown, use that corpus for both `/docs` and deterministic server side retrieval, and return citations selected from the retrieved source records. Use the configured Meta model through the gateway, with no model selector or new service.

## Rationale

The current portal already has the required tenant session, navigation, styling, and gateway integration. A separate Streamlit app would duplicate those systems. The approved corpus is intentionally small, so deterministic retrieval is easier to inspect and test than adding a vector database or retrieval framework. Sharing Markdown between the public page and retrieval avoids citations that point to stale or hidden content.

## Feature design

**Data model sketch**:
- `HelpDocument`: `document_id`, title, public Angular route, and Markdown path from a checked in `docs/help-center/manifest.json`.
- `HelpSection`: stable `section_id`, parent document ID, heading, optional summary, body text, and generated anchor. IDs are explicitly authored in Markdown metadata, match `[a-z0-9]+(?:-[a-z0-9]+)*`, and are never derived from headings; duplicate IDs fail the build.
- `ApiOperation`: namespaced `operation_id`, method, path, summary, description, request and response shape, and security requirements from the generated public `/openapi.json` contract created by `gateway/main.py:custom_openapi()`. The manifest maps each operation ID to a public Help Center section ID. Unknown or removed operation IDs fail corpus validation.
- `AssistantRequest`: transient question plus up to eight alternating `user` and `assistant` text messages. No database record is created; request and history text each follow AC-5 bounds.
- `AssistantResponse`: bounded `paragraphs`, each with plain text and one or more namespaced citation IDs, plus at most three citation records with title and server generated relative URL. Request usage has nullable input tokens, output tokens, and cost. Cost is returned only to a `billing:admin` principal; other principals receive null. No database record is created.
- `UsageRecord`: existing tenant scoped request completed event for every HTTP attempt, plus an existing token usage event only when complete provider token counts are available. Missing counts and cost remain unavailable, not zero or estimated.

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/v1/help/assistant` | POST | `question:string` (nonblank, max 4,000 Unicode code points), `history:list` (optional, max 8 bounded message objects) | `paragraphs:list` (`text`, `source_ids`), up to 3 `citations` (`source_id`, `title`, relative `url`), `usage` (`input_tokens`, `output_tokens`, `cost_usd`, nullable) | Verified user JWT via `verify_jwt`; no machine key or additional scope | 400 guardrail block, 401 invalid credentials, 403 missing tenant claim, 422 invalid shape or size, 429 with `Retry-After`, 502 invalid or rejected provider response, 503 timeout or unavailable |

**Value sourcing**:

| Action | Value produced / displayed | Source |
|---|---|---|
| Render Help Center | Page text, headings, anchors, and local links | `docs/help-center/manifest.json` plus the referenced Markdown; a checked in build step emits safe static page content and the retrieval artifact from the same source |
| Build corpus | Section IDs, titles, text, local links, and API operation map | `docs/help-center/manifest.json` plus referenced Markdown. A checked in build command emits `build/help-center-content.json`; Angular bundles its safe static page content, and the gateway image includes its retrieval records. The builder supports headings, paragraphs, lists, inline code, fenced code, and local links only. It runs whenever Markdown or the generated OpenAPI contract changes; duplicate IDs, invalid local links, or unmapped public operations fail the build or image build. |
| Retrieve sources | Up to 3 sections or operations and bounded context | Normalize with Unicode NFKC and case folding, split on non alphanumeric characters, remove this fixed stopword set (`a`, `an`, `and`, `are`, `as`, `at`, `be`, `by`, `can`, `do`, `for`, `from`, `how`, `i`, `in`, `is`, `it`, `me`, `my`, `of`, `on`, `or`, `the`, `to`, `what`, `when`, `where`, `which`, `who`, `why`, `with`, `you`, `your`); score each unique remaining token once at its highest matching field weight, 3 for title, 2 for heading or summary, and 1 for body or operation metadata; require score at least 2; sort by score descending then namespaced ID ascending; cap selected context at 12,000 characters. |
| Generate answer | Paragraph text with source IDs | Configured Meta model response over retrieved records; history is untrusted conversational context and never evidence. The provider is asked to return JSON paragraphs, each with plain `text` and `source_ids`; invalid JSON or schema fails closed to abstention. |
| Validate and cite answer | At most 3 citations and their relative URLs | Server validates every paragraph source ID against retrieved records, drops uncited or malformed model output in favor of the fixed abstention, deduplicates citations in first-use order, and resolves URLs only through the manifest to `/docs#<section-id>`. |
| Show suggestions | Three to five question suggestions and destination anchors | Curated `suggestions` entries in the corpus manifest, each with a known section ID |
| Show conversation | User and assistant messages, loading and error states | Current page memory and request state; history payload contains only bounded plain text, no citation objects |
| Record usage | Tenant, request outcome, and available token counts | Existing request completed telemetry for every attempt and `emit_token_usage` only when provider input and output counts are both present |
| Show request cost | Cost value or unavailable state | `estimate_cost_usd` and `gateway/config/prices.json` per 1,000 token rate, only for `billing:admin`; null unless counts and a verified model rate exist. For unpriced models the UI says “Cost unavailable”, never `$0`. |

**Key invariants**:
- Only documents in the curated corpus can support answers or citations.
- The manifest, page rendering, and retrieval artifact are generated from the same Markdown sources; no separate copied content is maintained.
- Citation IDs are namespaced as `section:<id>` or `operation:<operationId>`. Public citation URLs are relative `/docs#<section-id>` links obtained through the manifest mapping, never accepted from model output.
- Retrieval searches title, heading, summary, body, and public operation metadata. It uses the fixed stopword list, score and threshold in Value sourcing, returns at most three items and 12,000 context characters, and breaks ties by namespaced ID.
- The model receives the current question, bounded recent history, and retrieved approved documentation only. Every prior message is untrusted context and never evidence.
- Questions and history are normalized only for retrieval. The original text is scanned and redacted through existing guardrail functions before provider dispatch.
- No prompt text or conversation turn is persisted or logged.
- The client cannot select a tenant, model, provider, or retrieval corpus.
- Missing provider token usage never becomes an estimated token count or cost.

**Security model**:
- The public Help Center is read only and contains no tenant data.
- The assistant endpoint requires a verified user JWT through `verify_jwt`; machine API keys are rejected. Tenant identity comes from the verified token claim, not request fields or headers. No additional permission scope is required.
- Existing app level `verify_rate_limit` applies to the tenant before provider work. Existing prompt injection scanning applies to every submitted text field; configured PII redaction applies to user and history text before provider dispatch and to model output before return.
- The request completed event records method, path, status, latency, tenant, user, and trace. Assistant audit data is limited to model, outcome, status, and redaction counts. Neither question nor history may enter application, framework, provider exception, or telemetry logs.
- The configured provider receives the redacted question, bounded recent history, and retrieved public context. The page discloses provider processing before first submission.
- Model output is a bounded plain string, never HTML. The server discards model links and emits only validated relative documentation links.

**Configuration required**:
- No new environment variables or credentials. The endpoint uses the gateway's existing Meta model configuration and provider credentials.
- The configured model rate is considered verified only after the gateway operator confirms the model's USD per 1,000 token rate. Add only verified rates to `gateway/config/prices.json`; the existing `gateway/pricing.py` calculation remains the source for displayed cost.

The Assistant route is `/assistant`, guarded by the existing Angular `authGuard`; the Help Center links to it, and the sidebar uses the same route. Citations render as plain text labels and Angular router links directly under the paragraph that cites them. Clear cancels the active request or ignores its response, and component destruction ignores any late result. The page discloses that questions and recent conversation text are sent to the configured provider and are not retained by AgentShield.

**Critical test scenarios**:
- Happy path: signed in user asks about a mapped public operation, receives a supported answer whose paragraph markers resolve to retrieved sources and whose links are local `/docs` anchors, verifies **AC-2**, **AC-3**, **AC-6**, and **AC-7**.
- Retrieval boundaries: query score below 2 abstains; ties, duplicate anchors, invalid manifest links, and more than three candidates resolve deterministically or fail corpus validation, verifies **AC-2** and **AC-3**.
- Privacy and usage: no prompt or history appears in logs; full provider counts emit one token usage event; missing or partial counts emit none and stay unavailable; unpriced model cost is null, verifies **AC-8**.
- Failure case: provider timeout returns retryable 503, and the page retains the exact failed question; a 429 honors `Retry-After`, verifies **AC-5** and **AC-9**.
- Auth and limits: machine key, unauthenticated request, invalid message order, extra fields, and oversized question or history are rejected without provider calls, verifies **AC-5**, **AC-6**, and **AC-9**.
- Guardrails and rendering: injection in question or history blocks dispatch, PII is redacted, invalid citation markers yield abstention, and answer markup appears as text, verifies **AC-3**, **AC-7**, and **AC-8**.

## Build plan

The project uses Tracer Bullet delivery. First connect one complete question and citation through the corpus, authenticated endpoint, provider, and page. Then fill out the remaining source coverage and resilience behavior.

1. [x] Define and validate the manifest and Markdown conventions, stable IDs, allowlisted local links, and reviewed suggestions; generate the safe Help Center content and retrieval artifact from those same sources, satisfies **AC-2** and **AC-4**.
2. [x] Extract the allowed operation metadata from generated `/openapi.json`, maintain the operation ID to Help Center section map, and implement the fixed lexical scoring, result, and context bounds, satisfies **AC-2** and **AC-3**.
3. [x] Add the `verify_jwt` endpoint with strict request schema, bounded history, existing rate limiting and guardrails, configured nonstreaming Meta call, citation marker validation, abstention, and normalized safe errors, satisfies **AC-3**, **AC-6**, **AC-7**, and **AC-9**.
4. [x] Reuse request completed telemetry and emit token usage only for complete provider counts; return nullable request usage and cost with the existing `billing:admin` and verified rate card rules, satisfies **AC-8**.
5. [x] Build the signed in Assistant page, portal links, suggestions, cited thread, loading, retry, clear, provider disclosure, and browser memory behavior in the existing design, satisfies **AC-1**, **AC-4**, and **AC-5**.
6. Verify corpus validation, privacy, auth, rate limits, guardrail blocks, provider failures, rendering, request races, partial usage, cost gating, and abstention across the page and endpoint, satisfies **AC-3**, **AC-5**, **AC-7**, **AC-8**, and **AC-9**.

## Consequences

**Positive**:
- Users get documentation help without granting the assistant account operations.
- Citations lead to the same content shown in the public Help Center.
- Existing gateway controls provide tenant isolation, guardrails, telemetry, and metering.

**Negative / tradeoffs**:
- Curated Markdown and stable IDs need review when portal or API behavior changes.
- Deterministic retrieval may miss paraphrases that a larger semantic retrieval system could find.
- The model can still produce unsupported text; source constrained prompting, server selected citations, abstention, and verification reduce but cannot eliminate this risk.

**Neutral**:
- Chat is ephemeral and cannot be resumed after navigation or page close.
- Cost reporting is unavailable until a verified rate exists for the configured Meta model.

## Follow-up

- [ ] Confirm the source and rate for any Meta model price before displaying assistant cost.

## References

**Project sources**:
- `AGENTS.md`, project stack, workflow, and gateway operational conventions.
- `docs/portal-ui-design.md`, current portal design and responsive patterns.
- `docs/specs/0016-portal-help-center.md`, public Help Center requirements.
- `gateway/routers/meta_models.py`, configured Meta provider, guardrails, and content safe telemetry.
- `gateway/telemetry.py` and existing usage metering conventions.
- `gateway/config/prices.json`, current rate card source.

**Practices & standards**:
- Data minimization: retain only the data needed for the current request.
- Allowlist validation: citation targets are selected from known documentation routes.
- Defense in depth: retrieved evidence and model instructions do not replace gateway guardrails or output validation.
