# Spec 0017 Verification Checklist

Run these checks against a freshly rebuilt local stack. Do not use real tenant prompts or credentials in screenshots or logs.

## Build and routes

- [ ] From the repository root, regenerate the shared content with `.venv/bin/python docs/help-center/build.py`; confirm the command succeeds and updates both generated JSON artifacts.
- [ ] Run `docker compose up -d --build gateway-python portal-frontend` so the Python image contains the assistant corpus and the Angular bundle contains the public Help Center content.
- [ ] Open `http://localhost:4200/docs` in a signed-out browser. Confirm the Help Center renders its sections and shortcuts, and that it makes no tenant API request.
- [ ] Open `http://localhost:4200/assistant` signed out. Confirm the existing auth guard requires sign in.
- [ ] Sign in and open `/assistant`. Confirm the sidebar link, question suggestions, provider disclosure, composer, and Clear action render at desktop width and at 375 CSS pixels without horizontal page scrolling.

## Endpoint and assistant behavior

- [ ] Submit a question covered by the Help Center, such as “How do I create an API key?” Confirm a complete answer appears with local citations to the matching Help Center section or API operation.
- [ ] Submit an unsupported question. Confirm the assistant abstains, links to the Help Center, and suggests asking the workspace owner without claiming a source supports the answer.
- [ ] Submit a prompt injection attempt in the question and in prior conversation text. Confirm each is rejected before provider inference and no prompt text appears in logs or telemetry.
- [ ] Confirm the assistant response contains no `reasoning` or `reasoning_content` fields and that model supplied HTML renders only as text.
- [ ] Verify malformed request shapes, extra fields, invalid message ordering, empty input, overlong questions, and overlong history return the specified 422 response.
- [ ] Confirm a machine API key alone cannot call `/v1/help/assistant`, while an invalid or missing bearer token receives 401 and a verified token without a tenant receives 403.

## Failure, privacy, and usage

- [ ] Simulate provider timeout and provider rejection. Confirm timeout returns retryable 503 with `Retry-After`, provider rejection returns a safe 502, and the UI preserves the question for manual retry.
- [ ] Trigger tenant rate limiting. Confirm the response includes `Retry-After`, the UI waits before enabling retry, and it does not retry automatically.
- [ ] Clear the conversation while a request is pending and navigate away during a request. Confirm late responses do not repopulate the cleared or abandoned thread.
- [ ] Review gateway logs and tenant telemetry for the interaction. Confirm they include only allowed request metadata, outcome, model, status, and redaction counts—not prompt, history, or answer text.
- [ ] When both provider prompt and completion token counts are valid, confirm one token usage event is emitted. With either count missing or invalid, confirm no token event is emitted and token values remain unavailable.
- [ ] Confirm cost remains unavailable unless token counts are complete, the configured model has a verified rate in `gateway/config/prices.json`, and the user has `billing:admin`; confirm unavailable cost is never shown as `$0`.

## Corpus maintenance

- [ ] Change a suggestion or section in `docs/help-center`, regenerate the artifacts, and confirm the same updated content appears in `/docs` and assistant retrieval.
- [ ] Temporarily introduce an unknown operation ID, duplicate section ID, invalid local link, or unmapped public OpenAPI operation. Confirm corpus generation fails with the offending item identified, then restore the valid source.
