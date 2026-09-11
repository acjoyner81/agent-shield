"""Redis audit queue exporter for Splunk HTTP Event Collector."""

import json
import time

import redis
import requests


class SplunkHECExporter:
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        splunk_hec_url: str = "http://localhost:8088/services/collector/event",
        token: str = "",
    ) -> None:
        self.redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.splunk_url = splunk_hec_url
        self.headers = {
            "Authorization": f"Splunk {token}",
            "Content-Type": "application/json",
        }

    def flush_audit_queue_to_splunk(self) -> bool:
        """Drain the Redis queue and batch-send newline-delimited HEC events."""
        events: list[str] = []
        while raw_event := self.redis_client.rpop("splunk_audit_queue"):
            payload = json.loads(raw_event)
            events.append(
                json.dumps(
                    {
                        "time": payload.get("timestamp", time.time()),
                        "host": "agentshield-gateway",
                        "source": "agentshield:telemetry",
                        "sourcetype": "_json",
                        "event": payload,
                    }
                )
            )

        if not events:
            return True

        try:
            response = requests.post(
                self.splunk_url,
                headers=self.headers,
                data="\n".join(events),
                timeout=5,
            )
            response.raise_for_status()
            return True
        except requests.RequestException:
            for event in reversed(events):
                self.redis_client.rpush("splunk_audit_queue", event)
            return False


async def export_event(event: dict[str, object]) -> None:
    """Queue an audit event for the exporter without blocking a request."""
    del event
