"""
The one module in this repository that performs an HTTP call to Splunk's HTTP
Event Collector.

Every other producer (the gateway routes, the metering engine) writes to the
`telemetry:queue` stream and lets this container do the shipping, so the scheme,
the certificate trust, and the retry policy are configured in exactly one place.

Three invariants hold on every batch, in this order:

1. Personal data is scrubbed *before* the POST, so nothing unmasked leaves the
   process.
2. An HTTP 200 whose body reports a non-zero `code` is a **failure**. Splunk
   answers 200 with `{"code": 3, "text": "No data"}` for an event it refuses, and
   the previous implementation acknowledged those events away and destroyed them.
3. The stream entries are acknowledged last, and only once the batch is either
   confirmed shipped or safely copied into the dead letter queue.

The transport is TLS and the certificate is verified against the CA file named by
`SPLUNK_HEC_CA`. Verification is never disabled: the reason there is no flag to
turn it off is that this project has already had a security control fail open
quietly once.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import redis.asyncio as redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("telemetry-aggregator")

# Environment Variables
#
# The scheme in SPLUNK_HEC_URL is the load bearing part of this file. It shipped
# as http:// against a collector that only answers on TLS, so every attempt was
# reset by the peer before the token was ever sent.
SPLUNK_HEC_URL = os.environ.get("SPLUNK_HEC_URL", "https://splunk:8088/services/collector/event")
SPLUNK_HEC_TOKEN = os.environ.get("SPLUNK_HEC_TOKEN", "")
SPLUNK_HEC_CA = os.environ.get("SPLUNK_HEC_CA", "")
SPLUNK_HEC_CA_WAIT_SEC = int(os.environ.get("SPLUNK_HEC_CA_WAIT_SEC", "300"))
SPLUNK_HEC_INDEX = os.environ.get("SPLUNK_HEC_INDEX", "main")
SPLUNK_HEC_SOURCETYPE = os.environ.get("SPLUNK_HEC_SOURCETYPE", "agentshield:telemetry")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "100"))
BATCH_TIMEOUT_SEC = int(os.environ.get("BATCH_TIMEOUT_SEC", "5"))
TELEMETRY_DLQ_MAX = int(os.environ.get("TELEMETRY_DLQ_MAX", "10000"))
MAX_RETRIES = 5

# Redis Keys
QUEUE_KEY = "telemetry:queue"
DLQ_KEY = "telemetry:dlq"
SHIP_KEY = "telemetry:ship"
DLQ_DROPPED_KEY = "telemetry:dlq:dropped"
GROUP_NAME = "telemetry-aggregator"
CONSUMER_NAME = f"telemetry-aggregator-{os.getpid()}"

# PII patterns from 0001-telemetry-standard, compiled once at import.
#
# Compiled at module scope because the scrubber runs on every string of every
# shipped event; `re` caches compiled patterns anyway, but the intent is that
# this is a per-batch cost and not a per-string one.
_PII_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*"),
    re.compile(r"(sk|pk)_(live|test)_[0-9a-zA-Z]{24,}|mock_secret_key_[0-9]+"),
    re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
)

MASK = "[MASKED]"


def scrub_pii(text: str) -> str:
    """Mask a JSON web token, a Stripe-style key, and an email address."""
    for pattern in _PII_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def scrub_value(value: Any) -> Any:
    """Recursively mask string values, so the result is still valid JSON.

    Scrubbing the serialized text instead would be shorter, but a payload that
    no longer parses is worse than one with an unmasked string, and the spec asks
    for masking over string values specifically.
    """
    if isinstance(value, str):
        return scrub_pii(value)
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    return value


def scrub_payload(raw: str) -> str:
    """Scrub one queued payload, leaving it byte-identical if it is not JSON."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return scrub_pii(raw)
    return json.dumps(scrub_value(parsed), separators=(",", ":"))


class ShipRejected(Exception):
    """The collector answered, but refused the batch.

    Distinct from a transport failure because the batch is well formed and a
    retry cannot help: an invalid token or a malformed event stays refused.
    """


# The dead letter queue push, the cap, and the drop counter are one script.
#
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


async def record_attempt(r) -> None:
    """Stamp the attempt before the POST, so a hung ship still looks attempted."""
    try:
        await r.hset(SHIP_KEY, mapping={"last_attempt": f"{time.time():.3f}"})
    except Exception as exc:  # pragma: no cover - receipt is best effort
        logger.warning("Could not record ship attempt: %s", exc)


async def record_success(r) -> None:
    try:
        await r.hset(
            SHIP_KEY,
            mapping={
                "last_success": f"{time.time():.3f}",
                "last_error": "",
                "consecutive_failures": "0",
            },
        )
    except Exception as exc:  # pragma: no cover - receipt is best effort
        logger.warning("Could not record ship success: %s", exc)


async def record_failure(r, reason: str) -> None:
    """Record a non-empty reason and bump the consecutive failure count.

    `last_error` is only ever cleared by a success, so an idle stack does not
    carry a stale reason that reads as a live problem.
    """
    try:
        await r.hset(SHIP_KEY, mapping={"last_error": reason or "unknown failure"})
        await r.hincrby(SHIP_KEY, "consecutive_failures", 1)
    except Exception as exc:  # pragma: no cover - receipt is best effort
        logger.warning("Could not record ship failure: %s", exc)


def _body_code(response: httpx.Response) -> Tuple[int, str]:
    """Parse the collector's own verdict out of the response body.

    HEC answers HTTP 200 for a batch it refused, reporting the real outcome in
    the body as `code`. Treating the status alone as the outcome is what made a
    rejected batch look shipped.
    """
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return 0 if not text else -1, text[:200]
    if not isinstance(body, dict):
        return -1, str(body)[:200]
    return int(body.get("code", -1)), str(body.get("text", "") or "")


async def ship_to_splunk(client: httpx.AsyncClient, payloads: List[str]) -> None:
    """POST a batch and raise unless the collector's body reports code 0.

    The batch encoding matters and was measured against Splunk 10.4.3, because
    two encodings report `code 0` while destroying the batch:

    - `/raw` with a newline joined body indexes the whole request as ONE event
      whose `_raw` is the literal concatenated text.
    - `/event` with `{"event": [...]}` indexes the array as ONE event.

    Both mean a caller searching for a known event id finds nothing while the
    collector claims success. Newline delimited `{"event": {...}}` envelopes on
    `/event` is the form that indexes each event separately and makes them
    searchable by id, which is what AC-1 is verified against.
    """
    params = {"index": SPLUNK_HEC_INDEX, "sourcetype": SPLUNK_HEC_SOURCETYPE}
    headers = {"Authorization": f"Splunk {SPLUNK_HEC_TOKEN}"}
    envelopes = "\n".join(_envelope(payload) for payload in payloads)

    response = await client.post(SPLUNK_HEC_URL, params=params, content=envelopes, headers=headers)

    code, text = _body_code(response)
    response.raise_for_status()
    if code != 0:
        raise ShipRejected(f"collector rejected the batch: code={code} text={text!r}")


def _envelope(raw: str) -> str:
    """Wrap one queued payload in a HEC envelope, always on a single line.

    Scrubbing already re-serialized every JSON payload compactly, and json.dumps
    escapes any newline inside a string, so no payload can break the
    newline delimited framing above.
    """
    try:
        event = json.loads(raw)
    except (TypeError, ValueError):
        event = {"message": raw}
    return json.dumps({"event": event}, separators=(",", ":"))


async def wait_for_ca() -> str:
    """Block until the CA file exists, or refuse.

    The authority appears minutes into Splunk's boot, so a hard failure at
    process start is the difference between a slow start and a crash loop. The
    wait is bounded and explicit rather than implicit in a retry.
    """
    if not SPLUNK_HEC_CA:
        raise SystemExit(
            "SPLUNK_HEC_CA is not set. The collector certificate is verified "
            "against it, and verification is never skipped."
        )

    deadline = time.time() + SPLUNK_HEC_CA_WAIT_SEC
    while not os.path.exists(SPLUNK_HEC_CA):
        if time.time() >= deadline:
            raise SystemExit(
                f"SPLUNK_HEC_CA={SPLUNK_HEC_CA} did not appear within "
                f"{SPLUNK_HEC_CA_WAIT_SEC}s. Refusing to ship without verifying "
                "the collector's certificate."
            )
        logger.info(
            "Waiting for the Splunk certificate authority at %s "
            "(%.0fs of %ss)",
            SPLUNK_HEC_CA,
            SPLUNK_HEC_CA_WAIT_SEC - max(0.0, deadline - time.time()),
            SPLUNK_HEC_CA_WAIT_SEC,
        )
        await asyncio.sleep(5)
    return SPLUNK_HEC_CA


async def dump_to_stdout(batch: List[str]) -> None:
    """Emergency NDJSON fallback to stdout."""
    for log in batch:
        print(log)
        sys.stdout.flush()


async def ensure_consumer_group(r) -> None:
    try:
        await r.xgroup_create(name=QUEUE_KEY, groupname=GROUP_NAME, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def collect_batch(r, batch: List[Tuple[str, str]]) -> None:
    """Fill the batch, either to BATCH_SIZE or until the batch window closes."""
    start_time = time.time()
    while len(batch) < BATCH_SIZE:
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


async def ship_batch(client: httpx.AsyncClient, r, batch: List[Tuple[str, str]]) -> None:
    """Ship one batch with retries, then either ack it or dead letter it.

    The acknowledgement is the load bearing part and happens last. An event does
    not leave `telemetry:queue` until the collector reports code 0 or the event
    is in the dead letter queue, never one instead of the other.
    """
    payloads = [scrub_payload(payload) for _, payload in batch]

    reason: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 1):
        await record_attempt(r)
        try:
            await ship_to_splunk(client, payloads)
            await record_success(r)
            logger.info("Successfully shipped %d logs to Splunk.", len(payloads))
            await r.xack(QUEUE_KEY, GROUP_NAME, *[mid for mid, _ in batch])
            return
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}".strip()
            if isinstance(exc, ShipRejected):
                # A refusal is not transient. Retrying it five times only delays
                # the same dead letter entry.
                break
            wait = (2**attempt) * 0.1
            logger.warning(
                "Shipment failed (attempt %d/%d): %s. Retrying in %.2fs",
                attempt,
                MAX_RETRIES,
                reason,
                wait,
            )
            await asyncio.sleep(wait)

    assert reason is not None
    await record_failure(r, reason)
    logger.error("Batch failed, moving %d logs to %s: %s", len(payloads), DLQ_KEY, reason)

    pushed, dropped, depth = await dlq_push(r, payloads, TELEMETRY_DLQ_MAX)
    logger.info(
        "Dead letter queue: pushed=%d dropped=%d depth=%d", pushed, dropped, depth
    )
    await dump_to_stdout(payloads)
    await r.xack(QUEUE_KEY, GROUP_NAME, *[mid for mid, _ in batch])


async def run_aggregator() -> None:
    if not SPLUNK_HEC_TOKEN:
        raise SystemExit(
            "SPLUNK_HEC_TOKEN is not set. The collector token is a credential "
            "sent on every batch, so the aggregator refuses to start without it."
        )

    ca_path = await wait_for_ca()
    logger.info(
        "Starting Telemetry Aggregator (Batch: %d, Timeout: %ds, Endpoint: %s, CA: %s)",
        BATCH_SIZE,
        BATCH_TIMEOUT_SEC,
        SPLUNK_HEC_URL,
        ca_path,
    )

    r = redis.from_url(REDIS_URL, decode_responses=True)
    await ensure_consumer_group(r)
    async with httpx.AsyncClient(verify=ca_path) as client:
        while True:
            batch: List[Tuple[str, str]] = []
            await collect_batch(r, batch)
            if not batch:
                continue
            await ship_batch(client, r, batch)


if __name__ == "__main__":
    try:
        asyncio.run(run_aggregator())
    except KeyboardInterrupt:
        logger.info("Aggregator stopped by user.")
    except SystemExit:
        raise
    except Exception as exc:
        logger.exception("Aggregator crashed: %s", exc)
        sys.exit(1)