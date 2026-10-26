# Model Selector

**Status**: Proposed
**Date**: 2026-10-25

## Summary
Implement a system to allow tenants to select and manage their preferred LLM model. This enables the portal to show available models (including Meta hackathon models) and ensures the gateway routes requests to the chosen model while maintaining accurate cost estimation via a centralized rate card registry.

## Requirements

### Functional
- **AC-1**: The gateway must provide an endpoint to list all available models and their associated costs.
- **AC-2**: Authorized users must be able to set a default model for their tenant.
- **AC-3**: The gateway must resolve the model for every request using a priority chain: Request Body $\rightarrow$ Tenant Default $\rightarrow$ System Default.
- **AC-4**: The system must use a centralized rate card (linked to the model) to calculate estimated spend on the dashboard.
- **AC-5**: If a selected model is unavailable or fails, the gateway must automatically fall back to the system default.

### Security
- **AC-6**: The `POST /v1/models/default` endpoint must require the `billing:admin` permission.

## Decision

### Implementation Strategy
The system will use a static registry for model definitions and Redis for tenant preferences.

### Model Registry
A static configuration file (`gateway/models_registry.yaml`) will define all supported models.
- **Fields**: `model_id`, `display_name`, `provider_id`, `input_cost_per_1k`, `output_cost_per_1k`.
- **Reasoning**: For the hackathon, the model list is stable. A static file is faster to implement and deploy than a database table.

### Persistence
The tenant's choice will be stored in the existing Redis tenant configuration hash.
- **Key**: `tenant:<tenant_id>:config`
- **Field**: `default_model`
- **Value**: The `model_id` from the registry.

### API Surface
- `GET /v1/models`: Returns the full list of models from the registry.
- `POST /v1/models/default`: Updates the `default_model` field in the Redis tenant hash.

### Routing Logic
The gateway will resolve the target model in the following order:
1. Extract `model` from the request JSON body.
2. If absent, fetch `default_model` from the Redis tenant config.
3. If absent or invalid, use the system-wide hardcoded default (e.g., `muse-spark-1.3`).

## Build Plan

### Slice 1: Backend Registry & API
- [ ] Create `gateway/models_registry.yaml` with Meta hackathon models and rate cards.
- [ ] Implement `GET /v1/models` returning registry data.
- [ ] Implement `POST /v1/models/default` with `billing:admin` check.
- [ ] Update `gateway/auth.py` or relevant middleware to ensure permission check works.

### Slice 2: Gateway Routing Integration
- [ ] Update the routing logic to implement the priority chain (Request $\rightarrow$ Tenant Default $\rightarrow$ System Default).
- [ ] Implement the automatic fallback mechanism to system default on failure.
- [ ] Integrate the registry's rate cards into the usage metering cost calculation.

### Slice 3: Portal Model Management UI
- [ ] Create a "Model Settings" view/modal in the Portal.
- [ ] Implement the model list display (fetching from `GET /v1/models`).
- [ ] Implement the model selection (sending `POST /v1/models/default`).
- [ ] Add visual feedback for the currently active default model.

## Consequences
- **Positive**: Tenants can switch models without code changes.
- **Positive**: Costs are automatically updated based on the selected model's rate card.
- **Neutral**: Adding new models requires a gateway redeploy (acceptable for hackathon).

## Follow-up
- Consider moving the registry to a DB if the number of models grows significantly.
- Add a "Model Health" indicator to the model list in the portal.
