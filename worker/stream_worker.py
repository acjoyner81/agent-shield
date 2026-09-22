import os
import json
import asyncio
import logging
from typing import Dict, Any, List
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stream_worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY = os.getenv("TELEMETRY_STREAM_KEY", "agent_shield:telemetry_stream")
GROUP_NAME = os.getenv("CONSUMER_GROUP_NAME", "agent_shield_workers")
CONSUMER_NAME = os.getenv("CONSUMER_NAME", f"worker-{os.getpid()}")

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
BLOCK_TIME_MS = int(os.getenv("BLOCK_TIME_MS", "2000"))


class TelemetryWorker:
    def __init__(self, redis_url: str = REDIS_URL):
        self.redis_url = redis_url
        self.client = None
        self.is_running = False

    async def connect(self):
        self.client = redis.from_url(self.redis_url, decode_responses=True)
        await self._ensure_consumer_group()

    async def _ensure_consumer_group(self):
        try:
            await self.client.xgroup_create(
                name=STREAM_KEY,
                groupname=GROUP_NAME,
                id="0",
                mkstream=True
            )
            logger.info(f"Created consumer group '{GROUP_NAME}' on stream '{STREAM_KEY}'")
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug(f"Consumer group '{GROUP_NAME}' already exists.")
            else:
                raise e

    async def process_event(self, msg_id: str, payload: Dict[str, Any]):
        """
        Processes an individual telemetry message payload.
        Replace/extend with your database persistence logic (e.g. asyncpg/SQLAlchemy insert).
        """
        event_type = payload.get("event_type", "unknown")
        tenant_id = payload.get("tenant_id", "unknown")
        logger.debug(f"Processing event [{msg_id}] | type={event_type} | tenant={tenant_id}")

    async def run(self):
        if not self.client:
            await self.connect()

        self.is_running = True
        logger.info(f"Worker {CONSUMER_NAME} starting stream consumption...")

        while self.is_running:
            try:
                # Read new messages from the stream
                entries = await self.client.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={STREAM_KEY: ">"},
                    count=BATCH_SIZE,
                    block=BLOCK_TIME_MS
                )

                if not entries:
                    continue

                for stream_name, messages in entries:
                    msg_ids_to_ack = []
                    for msg_id, data in messages:
                        try:
                            # Parse JSON payload if stored as string, or handle flat key-value pairs
                            raw_payload = data.get("payload")
                            payload = json.loads(raw_payload) if raw_payload else data
                            
                            await self.process_event(msg_id, payload)
                            msg_ids_to_ack.append(msg_id)
                        except Exception as err:
                            logger.error(f"Failed to process message {msg_id}: {err}")

                    if msg_ids_to_ack:
                        await self.client.xack(STREAM_KEY, GROUP_NAME, *msg_ids_to_ack)

            except asyncio.CancelledError:
                logger.info("Worker run loop cancelled.")
                self.is_running = False
                break
            except Exception as e:
                logger.error(f"Unexpected error in stream loop: {e}")
                await asyncio.sleep(1)

    async def close(self):
        self.is_running = False
        if self.client:
            await self.client.aclose()


if __name__ == "__main__":
    worker = TelemetryWorker()
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        logger.info("Stopping worker gracefully...")
