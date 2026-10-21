# Get started with AgentShield

AgentShield gives tenant administrators and developers one place to manage API access, route model requests, and review usage and service health.

## API keys {#api-keys}

Create a tenant key from [API keys & access](/keys). Give it a recognizable name, copy the secret when it is shown, and store it in a secret manager. The portal does not show the full secret again. Revoke keys that are no longer needed. Key creation and rotation are restricted to permissions held by the signed-in principal.

## Make your first request {#first-request}

Send requests through the gateway using a tenant key in the `X-Tenant-API-Key` header. Use a model configured for your workspace; model names and providers are managed in [Models & routing](/models).

For the Meta model hosted through Baseten, configure `META_API_BASE_URL` and `BASETEN_API_KEY` for the gateway. The request uses the OpenAI-compatible chat completion shape:

```bash
curl -X POST http://localhost:8000/v1/meta/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-API-Key: <YOUR_TENANT_API_KEY>' \
  -d '{"model":"<CONFIGURED_META_MODEL>","messages":[{"role":"user","content":"Hello"}]}'
```

The gateway authenticates the tenant, applies configured ingress guardrails, and forwards eligible requests to the configured provider. Never put provider credentials in browser code or send them from a client.

## Usage and service health {#usage-health}

Open [Overview](/dashboard) for tenant usage and the current service health summary. Open [Observability](/logs) to review recent gateway events. A degraded or stale health result means the latest probe needs attention; check the service detail before treating it as recovered.

## Troubleshooting {#troubleshooting}

An authentication error usually means the key is missing, invalid, revoked, or sent in the wrong header. A permission error means the signed-in principal does not hold the required permission. A degraded service means a dependency probe failed or became stale; review its reported detail and the [Observability](/logs) page.

For API request and response schemas, use the gateway's API documentation at `/api/docs` in a running local deployment.
