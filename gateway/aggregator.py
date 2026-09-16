import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import List, Dict, Any

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
MAX_RETRIES = 5

# Redis Keys
QUEUE_KEY = "telemetry:queue"
DLQ_KEY = "telemetry:dlq"

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

async def run_aggregator():
    logger.info(f"Starting Telemetry Aggregator (Batch: {BATCH_SIZE}, Timeout: {BATCH_TIMEOUT_SEC}s)")
    
    r = redis.from_url(REDIS_URL, decode_responses=True)
    async with httpx.AsyncClient() as client:
        while True:
            batch = []
            start_time = time.time()

            # 1. Collect Batch
            while len(batch) < BATCH_SIZE:
                # Check timeout
                if time.time() - start_time >= BATCH_TIMEOUT_SEC:
                    break
                
                # Try to pop a log from the queue
                # Using LPOP for simplicity; in production we might use RPOPLPUSH for reliability
                log = await r.lpop(QUEUE_KEY)
                if log:
                    batch.append(log)
                else:
                    # No logs available, sleep briefly to avoid CPU spinning
                    await asyncio.sleep(0.1)

            if not batch:
                continue

            # 2. Ship with Retries
            success = False
            attempts = 0
            
            while attempts < MAX_RETRIES:
                try:
                    await ship_to_splunk(client, batch)
                    success = True
                    break
                except Exception as e:
                    attempts += 1
                    wait = (2 ** attempts) * 0.1 # Exponential backoff: 0.2, 0.4, 0.8...
                    logger.warning(f"Shipment failed (attempt {attempts}/{MAX_RETRIES}): {e}. Retrying in {wait:.2f}s...")
                    await asyncio.sleep(wait)

            # 3. Handle Outcome
            if success:
                logger.info(f"Successfully shipped {len(batch)} logs to Splunk.")
            else:
                # Move to DLQ
                logger.error(f"Max retries reached. Moving {len(batch)} logs to {DLQ_KEY}.")
                async with r.pipeline() as pipe:
                    for log in batch:
                        pipe.rpush(DLQ_KEY, log)
                    await pipe.execute()
                
                # Also dump to stdout as emergency fallback
                await dump_to_stdout(batch)

if __name__ == "__main__":
    try:
        asyncio.run(run_aggregator())
    except KeyboardInterrupt:
        logger.info("Aggregator stopped by user.")
    except Exception as e:
        logger.exception(f"Aggregator crashed: {e}")
        sys.exit(1)
