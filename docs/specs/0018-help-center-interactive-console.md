# 0018. Link the Help Center "first request" section to an interactive API console

**Date**: 2026-10-22
**Status**: Proposed

## Summary

The Help Center teaches a route that is not in the public contract and its security note reads as a blanket ban on putting any key in a browser. This spec points the section at the guarded public route `/v1/chat/completions`, makes the gateway Swagger console usable through the portal at `/api/docs` by fixing the schema base path and the docs renderer navigation, adds a link and credential steps, rewords the security note to forbid embedding provider credentials only, and corrects several stale pieces of Help Center content. The public API contract stays unchanged.

## Context

The "Make your first request" section currently instructs the reader to call `/v1/meta/chat/completions` and to configure `META_API_BASE_URL` and `BASETEN_API_KEY`. That path is deliberately absent from the public API schema (`gateway/routers/meta_models.py` has `include_in_schema=False`), so it teaches a route the curated contract does not publish while Spec 0012's six contract paths include the guarded `/v1/chat/completions`. The section also contains a backticked reference to `/api/docs` that is not clickable and has become misleading, and its security note forbids putting credentials in a browser at all.

The gateway serves Swagger UI at `/docs` with `openapi_url="/openapi.json"`. Through the portal, nginx rewrites `/api/...` to the gateway root, so the console is nominally reachable at `/api/docs`. However, the Swagger HTML declares `url: '/openapi.json'`, and the portal serves `/openapi.json` as 404 while only `/api/openapi.json` reaches the gateway, so the schema fails to load. In addition, the docs renderer turns any paragraph link starting with `/` into an Angular `[routerLink]`, so a link to `/api/docs` would be captured by the wildcard route instead of opening the proxied Swagger page. The console also exposes the whole published contract (including key operations) and its JavaScript assets load from `cdn.jsdelivr.net`. Without fixing these issues, pointing to the console does not work and the security note cannot be corrected honestly.

## Requirements

**User stories**:
- As a tenant developer reading the Help Center, I want the first-request section to show the guarded route my key actually authenticates so that the example matches the published contract.
- As a tenant developer, I want a working console link so that I can paste a key and run a real request against the published schema without writing a script first.
- As a reader of the security note, I want to know exactly what must never reach a browser so that I do not mistake the console for a violation.

**Acceptance criteria** (the contract, each criterion is IDed and independently checkable):
- **AC-1**: The "Make your first request" section (`docs/help-center/getting-started.md`) teaches `POST /v1/chat/completions` as the primary path. The curl example is exactly
  ```bash
  curl -X POST http://localhost:8000/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -H 'X-Tenant-API-Key: <YOUR_TENANT_API_KEY>' \
    -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}'
  ```
  The text states that `model` defaults to `gpt-4o`, `prompt` may be used instead of `messages`, and this route currently returns a gateway stub response (not a live provider call) unless Meta routing is configured separately.
- **AC-2**: The section contains a clickable paragraph link to the console at `/api/docs`. The console works through the portal: nginx proxies both the docs page and its schema (`/api/docs` and `/api/openapi.json`), the Swagger UI declares a schema URL that the portal serves (for example `url: '/api/openapi.json'`), the docs renderer emits a plain `href` for `/api/` links (full page navigation) so the link does not go through the Angular router wildcard, and `GET http://localhost:4200/api/docs` returns the Swagger UI shell and `GET http://localhost:4200/api/openapi.json` returns the OpenAPI JSON.
- **AC-3**: `docs/help-center/build.py` adds `/api/docs` to `known_routes`. The validator still fails closed on unknown routes. The allowlist is treated as a mixed portal/proxied route list and contains no duplicates. `gateway/tests/test_help_center_build.py` is updated to use the same allowlist (no hardcoded duplication) and asserts that the rendered paragraph `parts` contain the `/api/docs` URL.
- **AC-4**: The section tells the reader to create a key on the API keys page (`/keys`) and paste it into the console's `apiKey` (X-Tenant-API-Key) Authorize box (not necessarily the bearer box). It warns that the console is schema driven and exposes the whole published contract (including key operations), so a least privilege key is recommended.
- **AC-5**: The existing backticked sentence at line 32 ("For API request and response schemas, use the gateway's API documentation at `/api/docs` in a running local deployment.") is either replaced by the new link step or removed. The security note is rewritten to say never embed the gateway's provider secret (`BASETEN_API_KEY`) or ship provider base URLs in browser code; a tenant's own key typed into this gateway's console is not the thing being forbidden, and the note uses that exact distinction.
- **AC-6**: The Meta-specific guidance becomes a short marked gateway-side configuration note (e.g. a blockquote labeled "Gateway configuration for Meta models") stating that `BASETEN_API_KEY` is required for the hosted Meta model, `META_API_BASE_URL` is an optional override, and this is not needed for the console above. It no longer presents `/v1/meta/chat/completions` as the primary first request path. `docs/help-center/manifest.json` is updated (the suggestion "How do I make my first Meta model request?" becomes a generic "How do I make my first request?" or is replaced) so the landing suggestions match the section.
- **AC-7**: The two generated artifacts (`gateway/help_center_content.json` and `portal-frontend/public/help-center-content.json`) are rebuilt from the changed source and stay identical, and every rendered section still carries non-empty blocks (the shape guarantee pinned by `gateway/tests/test_help_center_build.py`). The build passes with the new link present.
- **AC-8**: The in-content paragraph link to `/api/docs` is rendered as a plain anchor (full page navigation) by the docs component, and list items that might contain links are handled correctly (links in lists must remain clickable if present). The public schema's `servers` or Swagger config are adjusted so the docs page's declared schema URL resolves through the portal (no 404). Only local Docker Compose is stated as the supported environment for this console link.

## Options considered

### Option 1: Make the gateway Swagger usable through the portal and link it from the Help Center

Fix the plumbing so the console actually works: have nginx proxy `/api/openapi.json` (or make the docs page fetch it), configure Swagger to use the `/api` base path (via `servers` or docs configuration), make the docs renderer emit a plain `href` and full page navigation for `/api/` links, add `/api/docs` to `known_routes`, update the test allowlist, fix the section content and manifest, and rebuild artifacts.

**Pros**:
- Uses the gateway's schema-driven console with no new UI surface.
- Public contract (Spec 0012) unchanged.
- Leverages existing auth schemes (apiKey and bearer) already in the public schema.

**Cons**:
- Requires portal renderer changes, nginx changes, and Swagger base-path configuration.
- Depends on Swagger UI assets loading from `cdn.jsdelivr.net` (trust boundary for pasted keys) and on internet access.
- Console exposes the whole published contract, not just chat.

### Option 2: Drop the console link and fix only the documentation

Teach the guarded `/v1/chat/completions` route in the section, reword the security note, update the manifest, and point users to the gateway's directly working console at port 8000 in plain text without adding an in-product link.

**Pros**:
- No portal/nginx/renderer changes. Lowest blast radius.
- Avoids the CDN trust boundary in the product link.

**Cons**:
- Less discoverable; users must know the port and path.
- Does not satisfy "link to a live console" as requested.
- Still needs the content fixes and manifest update.

### Option 3: Build an in-app Angular console page

Add a portal route and component that posts to `/v1/chat/completions` with a key the user enters, rendering the response.

**Pros**:
- First party, controllable UI and error copy, no CDN dependency, no Swagger base path issues.

**Cons**:
- New UI, new route, new tests, duplicates schema-driven form behavior, larger blast radius than plumbing an existing working console.

## Decision

**Chosen option**: Option 1: Make the gateway Swagger usable through the portal and link it from the Help Center

Make the console actually work through the portal (nginx schema proxy, Swagger base path, renderer full-page navigation for `/api/` links), add `/api/docs` to the Help Center link validator and update its tests, correct the section content (route, curl, credential step, security note, Meta note), update the manifest, rebuild artifacts, and document the local Docker Compose scope and CDN trust boundary.

## Rationale

The public contract already publishes `/v1/chat/completions`, and the gateway already serves Swagger. The gap is plumbing plus documentation: the portal cannot load the schema, and the docs renderer would redirect the link away. Fixing those gaps is necessary to make the requested console link usable, and it uses existing capabilities with no new API surface or new portal feature page. Option 2 satisfies only the documentation part and does not deliver the requested "live console" link. Option 3 duplicates Swagger's schema-driven form and adds a new UI surface for the same purpose. The cross-check showed the "already works" premise is false, so Option 1 is the right answer only if the plumbing is included explicitly, as this spec now does.

## Feature design

**Data model sketch**: None. No entities, columns, or stores change.

**State transitions**: None.

**API surface**: No new endpoint. The console is the gateway's existing Swagger UI (`/docs`, reached as `/api/docs`); the documented route is the existing `POST /v1/chat/completions` (currently a gateway stub in this environment).
| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /v1/chat/completions | POST | model (opt, default gpt-4o), messages[] or prompt (opt) | choices[] / stub response, usage may be present | key (`X-Tenant-API-Key`) or bearer | 400, 401, 402, 403, 422, 429 |

**Value sourcing** (every value each action produces, computes, or displays names where it comes from):
| Action | Value produced / displayed | Source |
|---|---|---|
| Section curl example | request path `/v1/chat/completions`, body, headers | Spec 0012 contract path and `gateway/main.py` LLMRequest schema (model default, prompt/messages) |
| Section curl example | note about stub vs live | observed behavior in `gateway/main.py` / route implementation |
| Console link target | `/api/docs` and schema URL | nginx `/api/` rewrite + FastAPI docs/openapi + Swagger base path / servers config |
| Docs renderer | plain href for `/api/` links | `portal-frontend/src/app/features/docs/docs/docs.html` change |
| Console credential step | key created on `/keys`, apiKey scheme, least privilege note | existing key management page and public schema security schemes |
| Security note | distinction between provider secrets and tenant key | cross-check requirement |
| Meta config note | `META_API_BASE_URL`, `BASETEN_API_KEY`, marked as gateway side, not primary | `gateway/routers/meta_models.py` and env config |
| Manifest suggestion | generic first-request suggestion | `docs/help-center/manifest.json` |
| Test allowlist | allowlist synchronized with build.py | `gateway/tests/test_help_center_build.py` |
| Rendered section blocks | section body | `docs/help-center/getting-started.md` via `docs/help-center/build.py` |

**Key invariants**:
- The public schema (Spec 0012) keeps exactly its six paths; this change adds none and removes none.
- `build.py` rejects any in-content link whose path is not in `known_routes`; `/api/docs` must be a member before the source uses it. The allowlist is synchronized with the test.
- The two generated artifacts remain byte-identical to each other.
- The docs page's declared schema URL must resolve through the portal (no 404) and Swagger must use the `/api` base path.
- Links to `/api/` in paragraph blocks must not be captured by the Angular router (full page navigation).
- The security distinction is explicit: provider secrets must not be shipped in browser code; a tenant's own key typed into this gateway console is the credential used for the request and must be least privilege.

**Security model**: The console is Swagger UI served by the gateway through the portal proxy. It loads JavaScript from `cdn.jsdelivr.net` (documented trust boundary) and exposes the whole published contract, so users must use a least privilege tenant key created on `/keys`. Provider credentials (`BASETEN_API_KEY`) and provider base URLs must never be embedded in browser code shipped to users. No new credential is introduced.

**Configuration required**: None new. The section documents existing gateway-side vars (`META_API_BASE_URL`, `BASETEN_API_KEY`) as gateway configuration only. This is supported for local Docker Compose only.

**Critical test scenarios** (each maps to an acceptance criterion in ## Requirements):
- Content: the section's curl matches AC-1 exactly, the paragraph contains a link to `/api/docs` rendered by the docs component as a plain href, the credential step names `/keys` and the `apiKey` Authorize box with a least-privilege note, the security note uses the exact distinction, the Meta note is a marked blockquote and the manifest suggestion is updated, verifies **AC-1**, **AC-4**, **AC-5**, **AC-6**.
- Build guard: running `docs/help-center/build.py` with `/api/docs` absent from `known_routes` fails; present, it succeeds. `gateway/tests/test_help_center_build.py` is updated (no duplicated allowlist) and asserts the rendered paragraph `parts` contain `/api/docs`, and that every rendered section has non-empty blocks, verifies **AC-3**, **AC-7**.
- Console plumbing: `GET http://localhost:4200/api/docs` returns Swagger UI, `GET http://localhost:4200/api/openapi.json` returns OpenAPI JSON (not 404), the HTML declares a schema URL that resolves through `/api/`, Swagger uses the `/api` base path/servers, and clicking the paragraph link performs full page navigation (not an Angular router redirect). The public schema lists `/v1/chat/completions` with `apiKey` and `bearer` schemes, verifies **AC-2**, **AC-8**.
- Line 32 resolved: the old backticked sentence is replaced or removed, no stale unlinked mention remains, verifies **AC-5**.

## Build plan

1. Make the console usable through the portal: add nginx proxy for `/api/openapi.json` (and any assets needed), configure FastAPI/Swagger to use `/api` base path or set `servers` so the schema URL works through the proxy, and change the docs renderer so paragraph links starting with `/api/` render as plain `href` anchors (full page navigation) with coverage for list items, satisfies **AC-2**, **AC-8**.
2. Update Help Center artifacts: add `/api/docs` to `known_routes` in `docs/help-center/build.py` and refactor the test to share the allowlist; rewrite `docs/help-center/getting-started.md` per AC-1, AC-4, AC-5, AC-6 (exact curl, console link in a paragraph, credential step, security note, marked Meta note, remove or replace line 32); update `docs/help-center/manifest.json` suggestion; rebuild both artifacts, satisfies **AC-1**, **AC-3**, **AC-4**, **AC-5**, **AC-6**, **AC-7**.
3. Verify against the live stack: portal `/api/docs` and `/api/openapi.json` return 200, the rendered section carries the link and blocks, the link navigates without Angular redirect, `gateway/tests/test_help_center_build.py` passes, and the console can load the schema with `apiKey`/`bearer` schemes visible, satisfies **AC-1**–**AC-8**.

## Consequences

**Positive**:
- The section matches the published contract and points readers at the guarded route.
- A working, schema-driven console becomes discoverable and actually loads through the portal.
- The security note becomes precise instead of apparently self-contradictory.
- Stale content (wrong path, stale suggestion, misleading backticked line) is corrected.

**Negative / tradeoffs**:
- The console link depends on Swagger UI assets loading from `cdn.jsdelivr.net` (trust boundary for pasted keys) and needs internet access to render fully.
- Making it work requires nginx, FastAPI/Swagger base-path, and portal renderer changes, not just content.
- The console exposes the whole published contract, so users must use a least-privilege key.
- Only local Docker Compose is supported for this in-product link.

**Neutral**:
- `known_routes` grows by one entry and the test is updated to share the allowlist.
- No new API surface or new portal page is added.

## Follow-up

- [ ] Self-host the Swagger UI assets to remove the `cdn.jsdelivr.net` dependency and the trust boundary for pasted tenant keys.
- [ ] Consider an in-app Angular console as a follow-up if CDN trust or the cross-cutting console exposure is unacceptable.

## References

**Project sources** (verifiable, in this repo):
- `docs/specs/0012-public-api/index.md` (the six-path curated contract) · `gateway/main.py` (`docs_url`, `openapi_url`, router mounts, LLMRequest, security schemes) · `gateway/routers/meta_models.py` (`include_in_schema=False`) · `portal-frontend/nginx.conf` (`/api/` rewrite) · `portal-frontend/src/app/features/docs/docs/docs.html` (renderer) · `docs/help-center/getting-started.md` · `docs/help-center/manifest.json` · `docs/help-center/build.py` (`known_routes`) · `gateway/tests/test_help_center_build.py`

**Practices & standards**:
- Documentation should match the published contract; a tested validator that rejects undocumented routes (build fails closed).
