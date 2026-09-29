"""Usage Metering Engine: consumer, roll-up aggregator, and summary endpoints (Spec 0009)."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from config.settings import settings
from gateway.auth import resolve_active_tenant
from gateway.pricing import estimate_cost_usd

logger = logging.getLogger("agentshield.metering")
router = APIRouter(prefix="/v1/usage", tags=["usage"])

# In-memory / Redis key formats:
# Ledger processed set: usage:processed_events (Set of event_id)
# Daily aggregate hash: usage:daily:{tenant_id}:{YYYY-MM-DD}:{model} -> input, output, total, requests
# Per tenant per date counters: usage:daily:{tenant_id}:{YYYY-MM-DD}:__meta__
# Monthly aggregate hash: usage:monthly:{tenant_id}:{YYYY-MM} -> total_tokens, total_requests

# Model segment reserved for per tenant per date counters, not a real model
META_MODEL = "__meta__"

# Dashboard counters carried on the __meta__ hash (Spec 0011 AC-3)
META_COUNTER_FIELDS = ("quality_passed", "failed_requests", "rate_limited_requests")

# Security and request events that feed the __meta__ counters
META_EVENT_COUNTERS = {
    "agentshield.security.authz_failure": "failed_requests",
    "agentshield.security.rate_limit_exceeded": "rate_limited_requests",
    "agentshield.telemetry.request.completed": "quality_passed",
}


class ModelUsageSummary(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int
    cost_usd: Optional[float] = None


class UsageTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    total_requests: int
    quality_passed: int = 0
    failed_requests: int = 0
    rate_limited_requests: int = 0
    estimated_cost_usd: Optional[float] = None


class UsageSummaryResponse(BaseModel):
    tenant_id: str
    period_start: str
    period_end: str
    totals: UsageTotals
    by_model: List[ModelUsageSummary]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Monthly summary for a billing admin",
                    "description": (
                        "`cost_usd` and `estimated_cost_usd` are populated only for a "
                        "principal holding `billing:admin` and stay null otherwise."
                    ),
                    "value": {
                        "tenant_id": "tenant_alpha",
                        "period_start": "2026-09-01",
                        "period_end": "2026-09-30",
                        "totals": {
                            "input_tokens": 3000,
                            "output_tokens": 2000,
                            "total_tokens": 5000,
                            "total_requests": 5,
                            "quality_passed": 4,
                            "failed_requests": 1,
                            "rate_limited_requests": 0,
                            "estimated_cost_usd": 0.01,
                        },
                        "by_model": [
                            {
                                "model": "gpt-4o",
                                "input_tokens": 3000,
                                "output_tokens": 2000,
                                "total_tokens": 5000,
                                "request_count": 5,
                                "cost_usd": 0.01,
                            }
                        ],
                    },
                }
            ]
        }
    )


def get_redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def process_token_event(raw_event: str, r_client: Optional[redis.Redis] = None) -> bool:
    """
    Parses and records a token usage CloudEvent idempotently (Spec 0009 AC-1, AC-2, AC-3).
    Routes malformed events to DLQ 'telemetry:dlq'.
    """
    if r_client is None:
        r_client = get_redis_client()

    try:
        if isinstance(raw_event, dict):
            event = raw_event
        else:
            event = json.loads(raw_event)

        if event.get("type") != "agentshield.token.usage":
            return True  # Ignore non-token usage events safely

        event_id = event.get("event_id")
        tenant_id = event.get("tenant_id")
        if not event_id or not tenant_id:
            raise ValueError("Missing required event_id or tenant_id")

        # 1. Claim the event and apply the roll-up in a single atomic step, so a
        # duplicate is dropped and a failure leaves nothing behind to redrive.
        timestamp_str = event.get("timestamp") or datetime.now(timezone.utc).isoformat()
        usage_date = timestamp_str[:10]
        usage_month = timestamp_str[:7]

        # 2. Extract metrics
        data = event.get("data", {})
        tokens = data.get("tokens", {}) if "tokens" in data else data
        input_tokens = int(tokens.get("input", 0))
        output_tokens = int(tokens.get("output", 0))
        total_tokens = int(tokens.get("total", input_tokens + output_tokens))
        model = str(tokens.get("model", "default"))

        # 3. Update roll-up aggregates
        applied = r_client.eval(
            _APPLY_TOKEN_USAGE,
            4,
            "usage:processed_events",
            f"usage:daily:{tenant_id}:{usage_date}:{model}",
            f"usage:models:{tenant_id}:{usage_date}",
            f"billing:usage:{tenant_id}:{usage_month}",
            event_id,
            input_tokens,
            output_tokens,
            total_tokens,
            model,
        )
        if not applied:
            logger.info(f"Duplicate token event {event_id} skipped.")
        return True

    except Exception as exc:
        logger.error(f"Failed to process token usage event: {exc}")
        try:
            r_client.lpush("telemetry:dlq", raw_event if isinstance(raw_event, str) else json.dumps(raw_event))
        except Exception:
            pass
        return False


# A CloudEvent's dedup claim and its roll-up writes must land together or not at
# all. Claiming first and writing second let a failure in between strand the
# event in the ledger with the aggregate unwritten, so the DLQ entry it was
# routed to redrived as a duplicate and the usage was lost for good. These
# scripts type check every key they touch before the claim, then claim and
# write in one step, so a rejected event leaves no trace and redrives cleanly.
_APPLY_TOKEN_USAGE = """
local ledger, daily, models, billing = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local kinds = {redis.call('TYPE', ledger).ok, redis.call('TYPE', daily).ok,
               redis.call('TYPE', billing).ok}
for i = 1, #kinds do
  if kinds[i] ~= 'none' and kinds[i] ~= 'set' and kinds[i] ~= 'hash' then
    return redis.error_reply('metering: expected a set or hash, found a ' .. kinds[i])
  end
end
if redis.call('SADD', ledger, ARGV[1]) == 0 then
  return 0
end
redis.call('HINCRBY', daily, 'input_tokens', ARGV[2])
redis.call('HINCRBY', daily, 'output_tokens', ARGV[3])
redis.call('HINCRBY', daily, 'total_tokens', ARGV[4])
redis.call('HINCRBY', daily, 'request_count', 1)
redis.call('SADD', models, ARGV[5])
redis.call('HINCRBY', billing, 'total_tokens', ARGV[4])
redis.call('HINCRBY', billing, 'request_count', 1)
return 1
"""

_APPLY_META_COUNT = """
local ledger, meta = KEYS[1], KEYS[2]
local kinds = {redis.call('TYPE', ledger).ok, redis.call('TYPE', meta).ok}
for i = 1, #kinds do
  if kinds[i] ~= 'none' and kinds[i] ~= 'set' and kinds[i] ~= 'hash' then
    return redis.error_reply('metering: expected a set or hash, found a ' .. kinds[i])
  end
end
if redis.call('SADD', ledger, ARGV[1]) == 0 then
  return 0
end
redis.call('HINCRBY', meta, ARGV[2], 1)
return 1
"""


def meta_daily_key(tenant_id: str, usage_date: str) -> str:
    """Per tenant per date counter hash, keyed like a model key so the scan filter covers it."""
    return f"usage:daily:{tenant_id}:{usage_date}:{META_MODEL}"


def process_meta_event(raw_event: Any, r_client: Optional[redis.Redis] = None) -> bool:
    """
    Folds a security or request event into the per tenant per date dashboard
    counters (Spec 0011 AC-3). Idempotent via the shared processed event ledger.
    Events that carry nothing to count are acknowledged without a write.
    """
    if r_client is None:
        r_client = get_redis_client()

    try:
        event = raw_event if isinstance(raw_event, dict) else json.loads(raw_event)

        event_type = event.get("type")
        counter = META_EVENT_COUNTERS.get(event_type)
        if counter is None:
            return True  # Not a counter bearing event, nothing to record

        data = event.get("data") or {}

        # A request only counts toward quality when it carries a passing eval flag
        if event_type == "agentshield.telemetry.request.completed" and not data.get("eval_passed"):
            return True

        event_id = event.get("event_id")
        tenant_id = event.get("tenant_id")
        if not event_id or not tenant_id:
            raise ValueError("Missing required event_id or tenant_id")

        timestamp_str = event.get("timestamp") or datetime.now(timezone.utc).isoformat()
        is_new = r_client.eval(
            _APPLY_META_COUNT,
            2,
            "usage:processed_events",
            meta_daily_key(tenant_id, timestamp_str[:10]),
            event_id,
            counter,
        )
        if not is_new:
            logger.info(f"Duplicate meta event {event_id} skipped.")
        return True

    except Exception as exc:
        logger.error(f"Failed to process meta event: {exc}")
        try:
            r_client.lpush("telemetry:dlq", raw_event if isinstance(raw_event, str) else json.dumps(raw_event))
        except Exception:
            pass
        return False


def get_tenant_usage_summary(
    tenant_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    r_client: Optional[redis.Redis] = None,
    include_cost: bool = False,
) -> UsageSummaryResponse:
    """
    Retrieves usage aggregates for a tenant across a date range.

    Cost is computed at read time and only when include_cost is set, which the
    endpoint derives from the caller's billing:admin permission (Spec 0011 AC-1, AC-2).
    """
    if r_client is None:
        r_client = get_redis_client()

    now = datetime.now(timezone.utc)
    if not start_date:
        start_date = now.strftime("%Y-%m-01")
    if not end_date:
        end_date = now.strftime("%Y-%m-%d")

    # Scan for matching daily keys: usage:daily:{tenant_id}:*
    pattern = f"usage:daily:{tenant_id}:*"
    models_map: Dict[str, Dict[str, int]] = {}
    meta_totals: Dict[str, int] = {field: 0 for field in META_COUNTER_FIELDS}

    cursor = 0
    while True:
        cursor, keys = r_client.scan(cursor, match=pattern, count=100)
        for key in keys:
            parts = key.split(":")
            if len(parts) >= 5:
                key_date = parts[3]
                model = parts[4]

                if start_date <= key_date <= end_date:
                    data = r_client.hgetall(key)

                    # The __meta__ key shares the date filter but is not a model
                    if model == META_MODEL:
                        for field in META_COUNTER_FIELDS:
                            try:
                                meta_totals[field] += int(data.get(field, 0) or 0)
                            except (TypeError, ValueError):
                                continue
                        continue

                    if model not in models_map:
                        models_map[model] = {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                            "request_count": 0,
                        }
                    models_map[model]["input_tokens"] += int(data.get("input_tokens", 0))
                    models_map[model]["output_tokens"] += int(data.get("output_tokens", 0))
                    models_map[model]["total_tokens"] += int(data.get("total_tokens", 0))
                    models_map[model]["request_count"] += int(data.get("request_count", 0))

        if cursor == 0:
            break

    total_in = sum(m["input_tokens"] for m in models_map.values())
    total_out = sum(m["output_tokens"] for m in models_map.values())
    total_tok = sum(m["total_tokens"] for m in models_map.values())
    total_req = sum(m["request_count"] for m in models_map.values())

    by_model = []
    for m, data in sorted(models_map.items()):
        entry = ModelUsageSummary(
            model=m,
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            total_tokens=data["total_tokens"],
            request_count=data["request_count"],
        )
        if include_cost:
            entry.cost_usd = estimate_cost_usd(m, data["total_tokens"])
        by_model.append(entry)

    estimated_cost = None
    if include_cost:
        estimated_cost = round(sum(m.cost_usd or 0.0 for m in by_model), 6)

    return UsageSummaryResponse(
        tenant_id=tenant_id,
        period_start=start_date,
        period_end=end_date,
        totals=UsageTotals(
            input_tokens=total_in,
            output_tokens=total_out,
            total_tokens=total_tok,
            total_requests=total_req,
            quality_passed=meta_totals["quality_passed"],
            failed_requests=meta_totals["failed_requests"],
            rate_limited_requests=meta_totals["rate_limited_requests"],
            estimated_cost_usd=estimated_cost,
        ),
        by_model=by_model,
    )


@router.get("/summary", response_model=UsageSummaryResponse)
async def get_usage_summary_endpoint(
    request: Request,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    tenant_id: str = Depends(resolve_active_tenant),
) -> UsageSummaryResponse:
    """
    AC-5: Retrieve usage summary for authenticated tenant.

    Cost fields are populated only for a principal holding billing:admin and
    stay null for everyone else, so usage is never withheld from a tenant
    member who cannot see spend (Spec 0011 AC-2, AC-6).
    """
    permissions = getattr(request.state, "permissions", None) or set()
    return get_tenant_usage_summary(
        tenant_id,
        start_date,
        end_date,
        include_cost="billing:admin" in permissions,
    )
