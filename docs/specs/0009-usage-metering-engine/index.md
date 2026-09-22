# 0009. Usage Metering Engine

**Date**: 2026-09-22

**Status**: Proposed

## Summary

Defines the architecture and data processing pipeline for the Usage Metering Engine in AgentShield. The Usage Metering Engine consumes token usage CloudEvents (`agentshield.token.usage` defined in spec 0008) from Redis streams, aggregates cumulative token consumption per tenant across configurable billing windows, and syncs usage metrics to Stripe Metered Billing (spec 0007) while maintaining an auditable local ledger in PostgreSQL.

## Context

AgentShield functions as an enterprise multi-tenant AI gateway. Spec 0007 established the Stripe hybrid billing strategy, and spec 0008 defined the CloudEvents telemetry schemacarrying structured token metrics (`data.tokens.input`, `data.tokens.output`, `data.tokens.total`, and `data.tokens.model`).

Currently, while gateway services emit `agentshield.token.usage` events into the Redis stream (`telemetry:queue`), there is no worker process consuming these events to track tenant consumption or report usage to payment providers.

This creates three critical gaps:

1. **Revenue Leakage**: Token usage across LLM provider calls is not aggregated, preventing usage-based billing or overage charges.
2. **Lack of Tenant Visibility**: Tenants cannot query real-time token consumption or view broken-down historical usage by model or feature.
3. **Double-Counting & Sync Reliability**: Transmitting raw event-by-event API calls directly to external payment gateways (e.g., Stripe) introduces rate-limit bottlenecks and lacks idempotency, leading to duplicate charges or unbilled usage upon network failure.

## Requirements

**User stories**:

* As a gateway system, I want token usage events to be processed asynchronously from Redis so that API call latencies are not impacted by metering operations.
* As a finance engineer, I want usage data to be aggregated locally and reported idempotently to Stripe Metered Billing so that customers are accurately billed without duplicate charges.
* As a tenant administrator, I want to query usage metrics broken down by model and billing period so that I can monitor LLM expenditure.

**Acceptance criteria**:

* **AC-1**: The metering service consumes `agentshield.token.usage` events from the Redis `telemetry:queue` stream without blocking gateway request paths.
* **AC-2**: Token usage events are recorded idempotently in a PostgreSQL `usage_ledger` using the event's unique `event_id` to prevent double counting.
* **AC-3**: Cumulative token counts (`input`, `output`, `total`) are aggregated per `tenant_id` and `model` in a PostgreSQL `tenant_daily_usage` roll-up table.
* **AC-4**: A background sync worker batches aggregated tenant usage and reports metered usage to Stripe using Stripe Usage Records API (spec 0007), recording the Stripe response status in the ledger.
* **AC-5**: The API exposes a REST endpoint `/v1/usage/summary` allowing authenticated tenants to retrieve their current billing period usage metrics.
* **AC-6**: Unparseable or malformed token events are routed to a dead-letter queue (`telemetry:dlq`) for inspection without crashing the consumer worker.

## Options considered

### Option 1: Redis Stream Consumer + PostgreSQL Aggregation + Periodic Stripe Sync (Recommended)

An asynchronous background worker consumes events from Redis (`telemetry:queue`) using consumer groups. Events are written to a PostgreSQL append-only event ledger and rolled up into daily/hourly aggregate tables. A scheduled sync process batches the aggregated token deltas and submits usage records to Stripe Metered Billing using idempotent requests.

**Pros**:

* Decouples gateway request performance from database writes and external HTTP calls.
* Provides a persistent, audit-ready local source of truth (`usage_ledger`) in PostgreSQL.
* Batching updates to Stripe avoids hitting Stripe API rate limits.
* Exact-once processing guarantees via PostgreSQL `event_id` unique constraints.

**Cons**:

* Requires dedicated worker background processes or async loops.
* Requires managing consumer group offsets and dead-letter queues.

### Option 2: Direct Synchronous Push to Stripe from Gateway

The Python gateway process synchronously posts usage records to Stripe directly inside the `/v1/chat/completions` request-response path.

**Pros**:

* Simple architecture with no extra database tables or background workers.

**Cons**:

* Adds 100–300 ms of network latency to every LLM request.
* Gateway failure during Stripe API outages results in unbilled token usage.
* High risk of hitting Stripe API rate limits under heavy traffic.

### Option 3: In-Memory Aggregation in Redis Only

Token counts are incremented directly in Redis keys (`usage:{tenant_id}:{date}`) using `INCRBY` and periodically dumped to Stripe, skipping PostgreSQL.

**Pros**:

* Fast execution with minimal database overhead.

**Cons**:

* Loss of raw event history and loss of audit trailing.
* Redis node failure or eviction policies could erase unbilled tenant usage.
* Lacks granular transaction tracking per `event_id`.

## Decision

**Chosen option**: Option 1: Redis Stream Consumer + PostgreSQL Aggregation + Periodic Stripe Sync.

Token usage CloudEvents (`agentshield.token.usage`) are processed asynchronously via Redis Consumer Groups into PostgreSQL. Local aggregation tables maintain tenant usage balances, while a background sync task flushes usage deltas to Stripe Metered Billing.

**Implementation skills**: `fastapi` (`gateway/`) · `redis` (`redis/redis-py`) · `sqlalchemy` / `postgresql` · `stripe`

## Rationale

Option 1 provides the durability and auditability necessary for financial billing engines. By buffering events in Redis and persisting them idempotently in PostgreSQL, AgentShield guarantees that no token consumption is lost during system restarts or Stripe API outages. Decoupling the gateway response path ensures low latency for LLM proxy calls.

## Feature Design

### Data Model

#### 1. PostgreSQL Ledger Table (`usage_ledger`)

Stores raw token usage events for exact auditability and deduplication.

```sql
CREATE TABLE usage_ledger (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) UNIQUE NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    trace_id VARCHAR(64) NOT NULL,
    model VARCHAR(64) NOT NULL,
    input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    total_tokens INT NOT NULL DEFAULT 0,
    stripe_synced BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_usage_ledger_tenant_created ON usage_ledger (tenant_id, created_at);
CREATE INDEX idx_usage_ledger_stripe_synced ON usage_ledger (stripe_synced) WHERE stripe_synced = FALSE;

```

#### 2. PostgreSQL Daily Aggregate Table (`tenant_daily_usage`)

Pre-aggregated roll-up table for fast query responses on customer dashboards and billing reports.

```sql
CREATE TABLE tenant_daily_usage (
    tenant_id VARCHAR(64) NOT NULL,
    usage_date DATE NOT NULL,
    model VARCHAR(64) NOT NULL,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens BIGINT NOT NULL DEFAULT 0,
    request_count INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, usage_date, model)
);

```

---

### Processing Flow

```
+------------------+       +-------------------+       +-----------------------+
|  Gateway Emits   | ----> |   Redis Stream    | ----> | Usage Consumer Worker |
|  CloudEvent      |       | (telemetry:queue) |       | (XREADGROUP)          |
+------------------+       +-------------------+       +-----------------------+
                                                                   |
                                                                   v
                                                       +-----------------------+
                                                       | Insert `usage_ledger` |
                                                       | Update Aggregate Table|
                                                       +-----------------------+
                                                                   |
                                                                   v
                                                       +-----------------------+
                                                       | Scheduled Sync Worker |
                                                       | -> Stripe Usage API   |
                                                       +-----------------------+

```

1. **Ingestion**: The gateway emits `agentshield.token.usage` CloudEvent to Redis key `telemetry:queue`.
2. **Consumption**: `UsageConsumer` reads from `telemetry:queue` via consumer group `metering-group`.
3. **Ledger Insert**: Worker attempts `INSERT INTO usage_ledger` using `event_id`. If `ON CONFLICT (event_id) DO NOTHING` triggers, the event is acknowledged as a duplicate.
4. **Aggregate Rollup**: An upsert updates `tenant_daily_usage`:
```sql
INSERT INTO tenant_daily_usage (tenant_id, usage_date, model, input_tokens, output_tokens, total_tokens, request_count)
VALUES (:tenant_id, :usage_date, :model, :input_tokens, :output_tokens, :total_tokens, 1)
ON CONFLICT (tenant_id, usage_date, model) DO UPDATE SET
    input_tokens = tenant_daily_usage.input_tokens + EXCLUDED.input_tokens,
    output_tokens = tenant_daily_usage.output_tokens + EXCLUDED.output_tokens,
    total_tokens = tenant_daily_usage.total_tokens + EXCLUDED.total_tokens,
    request_count = tenant_daily_usage.request_count + 1,
    updated_at = CURRENT_TIMESTAMP;

```


5. **Stripe Sync**: A background cron/loop fetches un-synced totals grouped by `tenant_id`, calls `stripe.SubscriptionItem.create_usage_record()`, and sets `stripe_synced = TRUE` in the ledger.

---

### API Surface

#### `GET /v1/usage/summary`

Retrieves usage summary for the authenticated tenant.

**Headers**:

* `Authorization: Bearer <jwt>` or `X-Tenant-API-Key: <key>`

**Query Parameters**:

* `start_date` (optional, YYYY-MM-DD): Start of summary range. Defaults to 1st day of current month.
* `end_date` (optional, YYYY-MM-DD): End of summary range. Defaults to today.

**Response (200 OK)**:

```json
{
  "tenant_id": "ten_prod_enterprise_01",
  "period_start": "2026-09-01",
  "period_end": "2026-09-22",
  "totals": {
    "input_tokens": 1250000,
    "output_tokens": 450000,
    "total_tokens": 1700000,
    "total_requests": 3420
  },
  "by_model": [
    {
      "model": "gpt-4o",
      "input_tokens": 1000000,
      "output_tokens": 350000,
      "total_tokens": 1350000,
      "request_count": 2100
    },
    {
      "model": "claude-3-5-sonnet",
      "input_tokens": 250000,
      "output_tokens": 100000,
      "total_tokens": 350000,
      "request_count": 1320
    }
  ]
}

```

---

### Value Sourcing

| Action / Field | Source / Calculation |
| --- | --- |
| `event_id` | CloudEvent `event_id` from spec 0008 |
| `input_tokens` | `data.tokens.input` |
| `output_tokens` | `data.tokens.output` |
| `total_tokens` | `data.tokens.total` |
| `model` | `data.tokens.model` |
| Stripe Usage Quantity | Sum of `total_tokens` (or metered unit calculated per tier) |

---

### Security & Invariants

* **Exact-Once Financial Record**: `event_id` uniqueness ensures token usage cannot be counted multiple times.
* **Tenant Isolation**: `/v1/usage/summary` enforces tenant scoping via `get_verified_tenant` dependency.
* **Resilience**: If PostgreSQL is down, Redis buffers events until the consumer recovers.

---

### Critical Test Scenarios

* **Happy Path Ingestion**: Emit `agentshield.token.usage` CloudEvent to Redis -> consumer inserts into `usage_ledger` and updates `tenant_daily_usage`. (AC-1, AC-2, AC-3)
* **Deduplication**: Emit the same CloudEvent twice -> `usage_ledger` accepts only one row; aggregate counts reflect a single event. (AC-2)
* **Stripe Sync Execution**: Execute sync task -> verify Stripe Usage Record API is called with aggregated quantity and `stripe_synced` set to `True`. (AC-4)
* **Usage Summary API**: Query `/v1/usage/summary` as an authenticated tenant -> verify returned token breakdown matches aggregate database records. (AC-5)
* **Dead-Letter Handling**: Push invalid JSON to `telemetry:queue` -> verify message moves to `telemetry:dlq` without terminating worker loop. (AC-6)

---

## Canonical Implementation Pattern

```python
# gateway/metering/consumer.py
import json
import logging
from sqlalchemy.dialects.postgresql import insert
from gateway.db import SessionLocal
from gateway.models import UsageLedger, TenantDailyUsage

logger = logging.getLogger("agentshield.metering")

async def process_token_event(raw_event: str) -> bool:
    """Parses and records a token usage CloudEvent idempotently."""
    try:
        event = json.loads(raw_event)
        if event.get("type") != "agentshield.token.usage":
            return True  # Skip non-token events

        data = event.get("data", {})
        tokens = data.get("tokens", {})

        event_id = event["event_id"]
        tenant_id = event["tenant_id"]
        user_id = event.get("user_id", "unknown")
        trace_id = event.get("trace_id", "unknown")
        model = tokens.get("model", "unknown")
        input_tokens = tokens.get("input", 0)
        output_tokens = tokens.get("output", 0)
        total_tokens = tokens.get("total", 0)

        with SessionLocal() as db:
            # 1. Idempotent Insert into usage_ledger
            stmt = insert(UsageLedger).values(
                event_id=event_id,
                tenant_id=tenant_id,
                user_id=user_id,
                trace_id=trace_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ).on_conflict_do_nothing(index_elements=['event_id'])
            
            result = db.execute(stmt)
            
            # If row was inserted (not a duplicate), update aggregates
            if result.rowcount > 0:
                usage_date = event["timestamp"][:10]  # YYYY-MM-DD
                
                agg_stmt = insert(TenantDailyUsage).values(
                    tenant_id=tenant_id,
                    usage_date=usage_date,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    request_count=1
                ).on_conflict_do_update(
                    index_elements=['tenant_id', 'usage_date', 'model'],
                    set_={
                        'input_tokens': TenantDailyUsage.input_tokens + input_tokens,
                        'output_tokens': TenantDailyUsage.output_tokens + output_tokens,
                        'total_tokens': TenantDailyUsage.total_tokens + total_tokens,
                        'request_count': TenantDailyUsage.request_count + 1,
                    }
                )
                db.execute(agg_stmt)
            
            db.commit()
            return True

    except Exception as exc:
        logger.error(f"Failed to process token usage event: {exc}")
        return False

```

---

## Rollout & Follow-up

* [ ] Create SQLAlchemy models and database migrations for `usage_ledger` and `tenant_daily_usage`.
* [ ] Implement `UsageConsumer` worker service for Redis `telemetry:queue`.
* [ ] Implement background Stripe synchronization task for unbilled usage records.
* [ ] Add `/v1/usage/summary` REST endpoint in FastAPI gateway.
* [ ] Add test suite (`gateway/tests/test_metering.py`) verifying ingestion, deduplication, and sync scenarios.