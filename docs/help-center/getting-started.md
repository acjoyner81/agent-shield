# Get started with AgentShield

AgentShield gives tenant administrators and developers one place to manage API access, route model requests, and review usage and service health.

## API keys {#api-keys}

Create a tenant key from [API keys & access](/keys). Give it a recognizable name, copy the secret when it is shown, and store it in a secret manager. The portal does not show the full secret again. Revoke keys that are no longer needed. Key creation and rotation are restricted to permissions held by the signed-in principal.

## Make your first request {#first-request}

Create a key on [API keys & access](/keys) and keep it in a secret manager. Send requests through the gateway using a tenant key in the `X-Tenant-API-Key` header. Use a model configured for your workspace; model names and providers are managed in [Models & routing](/models).

You can try a live request in the interactive [API console](/api/docs). Use the `apiKey` (X-Tenant-API-Key) Authorize box with a least privilege tenant key you created on [API keys & access](/keys). The console is schema driven and exposes the whole published contract, so only paste keys you intend to use there.

Use the guarded chat completion route:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-API-Key: <YOUR_TENANT_API_KEY>' \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}'
```

`model` defaults to `gpt-4o`. You may use `prompt` instead of `messages`. In the current environment this route returns a gateway stub response, not a live provider call unless Meta routing is configured separately.

> **Gateway configuration for Meta models:** For the hosted Meta model, set the gateway's `BASETEN_API_KEY`; `META_API_BASE_URL` is an optional endpoint override. Neither setting is needed for the console above.

The gateway authenticates the tenant, applies configured ingress guardrails, and forwards eligible requests to the configured provider. Never embed the gateway's provider secret (`BASETEN_API_KEY`) or ship provider base URLs in browser code. Your own tenant key typed into this gateway's console is not forbidden, but never commit or ship it. This console link is supported for local Docker Compose only and loads Swagger UI assets from a CDN.

## Usage and service health {#usage-health}

Open [Overview](/dashboard) for tenant usage and the current service health summary. Open [Observability](/logs) to review recent gateway events. A degraded or stale health result means the latest probe needs attention; check the service detail before treating it as recovered.

## Troubleshooting {#troubleshooting}

An authentication error usually means the key is missing, invalid, revoked, or sent in the wrong header. A permission error means the signed-in principal does not hold the required permission. A degraded service means a dependency probe failed or became stale; review its reported detail and the [Observability](/logs) page.
