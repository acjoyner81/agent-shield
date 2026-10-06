import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import List, Dict, Any, Tuple

import httpx
import redis.asyncio as redis

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("telemetry-aggregator")

# Environment Variables
SPLUNK_HEC_URL = os.environ.get("SPLUNK_HEC_URL", "http://splunk:8088/services/collector/raw")
SPLUNK_HEC_TOKEN = os.environ.get("SPLUNK_HEC_TOKEN", "mock-token")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "100"))
BATCH_TIMEOUT_SEC = int(os.environ.get("BATCH_TIMEOUT_SEC", "5"))
TELEMETRY_DLQ_MAX = int(os.environ.get("TELEMETRY_DLQ_MAX", "10000"))
MAX_RETRIES = 5

# Redis Keys
QUEUE_KEY = "telemetry:queue"
DLQ_KEY = "telemetry:dlq"
DLQ_DROPPED_KEY = "telemetry:dlq:dropped"
GROUP_NAME = "telemetry-aggregator"
CONSUMER_NAME = f"telemetry-aggregator-{os.getpid()}"

async def scrub_pii(text: str) -> str:
    """Simple PII masking as defined in 0001-telemetry-standard."""
    # In a real app, these would be pre-compiled regexes
    import re
    patterns = {
        "jwt": r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*",
        "api_key": r"(sk|pk)_(live|test)_[0-9a-zA-Z]{24,}|mock_secret_key_[0-9]+",
        "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    }
    for pattern in patterns.values():
        text = re.sub(pattern, "[MASKED]", text)
    return text

async def scrub_pii_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursive PII masking for dictionaries."""
    if not isinstance(data, dict):
        return data
    
    scrubbed = {}
    for k, v in data.items():
        if isinstance(v, str):
            scrubbed[k] = await scrub_pii(v)
        elif isinstance(v, dict):
            scrubbed[k] = await scrub_pii_dict(v)
        else:
            scrubbed[k] = v
    return scrubbed

async def ship_to_splunk(client: httpx.AsyncClient, batch: List[str]):
    """Ship a batch of logs to Splunk HEC."""
    # Splunk HEC /raw endpoint expects multiple events concatenated or as a single payload
    # For /raw, events are sent as raw strings. We send them as JSON lines.
    payload = "\n".join(batch)
    headers = {"Authorization": f"Splunk {SPLUNK_HEC_TOKEN}"}
    
    response = await client.post(SPLUNK_HEC_URL, content=payload, headers=headers, timeout=10.0)
    response.raise_for_status()

async def dump_to_stdout(batch: List[str]):
    """Emergency NDJSON fallback to stdout."""
    for log in batch:
        # Ensure it's one JSON object per line
        print(log)
        sys.stdout.flush()

# The counter lives in the same script as the trim that caused the overflow, so
# the reported drop total can never drift from the data it describes. The push
# is RPUSH and the trim keeps the tail, so every writer adds at one end and a
# replay that pops that same end can always reach every entry.
DLQ_PUSH_LUA = """
local key = KEYS[1]
local maxn = tonumber(ARGV[1])
local n = #ARGV - 1
if n <= 0 then
  return {0, 0, redis.call('LLEN', key)}
end
if n == 1 then
  redis.call('RPUSH', key, ARGV[2])
else
  redis.call('RPUSH', key, unpack(ARGV, 2))
end
local dropped = 0
if maxn > 0 then
  local after = redis.call('LLEN', key)
  if after > maxn then
    dropped = after - maxn
    redis.call('LTRIM', key, -maxn, -1)
    redis.call('INCRBY', KEYS[2], dropped)
  end
end
return {n, dropped, redis.call('LLEN', key)}
"""


async def dlq_push(r, payloads: List[str], max_entries: int) -> Tuple[int, int, int]:
    """Move a batch to the dead letter queue. Returns (pushed, dropped, depth)."""
    if not payloads:
        return 0, 0, int(await r.llen(DLQ_KEY))
    result = await r.eval(
        DLQ_PUSH_LUA,
        2,
        DLQ_KEY,
        DLQ_DROPPED_KEY,
        str(max_entries),
        *payloads,
    )
    return int(result[0]), int(result[1]), int(result[2])

async def ensure_consumer_group(client):
    try:
        await client.xgroup_create(
            name=QUEUE_KEY,
            groupname=GROUP_NAME,
            id="0",
            mkstream=True,
        )
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

async def run_aggregator():
    logger.info(f"Starting Telemetry Aggregator (Batch: {BATCH_SIZE}, Timeout: {BATCH_TIMEOUT_SEC}s)")
    
    r = redis.from_url(REDIS_URL, decode_responses=True)
    await ensure_consumer_group(r)
    async with httpx.AsyncClient() as client:
        while True:
            batch: List[tuple[str, str]] = []
            start_time = time.time()

            # 1. Collect Batch
            while len(batch) < BATCH_SIZE:
                # Check timeout
                if time.time() - start_time >= BATCH_TIMEOUT_SEC:
                    break

                entries = await r.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={QUEUE_KEY: ">"},
                    count=BATCH_SIZE - len(batch),
                    block=100,
                )
                for _, messages in entries:
                    for message_id, fields in messages:
                        payload = fields.get("payload")
                        if payload is not None:
                            batch.append((message_id, payload))

            if not batch:
                continue

            # 2. Ship with Retries
            success = False
            attempts = 0
            logs = [payload for _, payload in batch]

            while attempts < MAX_RETRIES:
                try:
                    await ship_to_splunk(client, logs)
                    success = True
                    break
                except Exception as e:
                    attempts += 1
                    wait = (2 ** attempts) * 0.1 # Exponential backoff: 0.2, 0.4, 0.8...
                    logger.warning(f"Shipment failed (attempt {attempts}/{MAX_RETRIES}): {e}. Retrying in {wait:.2f}s...")
                    await asyncio.sleep(wait)

            # 3. Handle Outcome
            if success:
                logger.info(f"Successfully shipped {len(logs)} logs to Splunk.")
            else:
                # Move to DLQ
                logger.error(f"Max retries reached. Moving {len(logs)} logs to {DLQ_KEY}.")
                pushed, dropped, depth = await dlq_push(r, logs, TELEMETRY_DLQ_MAX)
                if dropped:
                    logger.warning(f"DLQ trimmed {dropped} entries to stay within {TELEMETRY_DLQ_MAX}. Depth now {depth}.")

                # Also dump to stdout as emergency fallback
                await dump_to_stdout(logs)

            message_ids = [message_id for message_id, _ in batch]
            await r.xack(QUEUE_KEY, GROUP_NAME, *message_ids)

if __name__ == "__main__":
    try:
        asyncio.run(run_aggregator())
    except KeyboardInterrupt:
        logger.info("Aggregator stopped by user.")
    except Exception as e:
        logger.exception(f"Aggregator crashed: {e}")
        sys.exit(1)
