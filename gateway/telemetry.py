import json
import redis
from datetime import datetime
from config.settings import settings

r = redis.Redis.from_url(settings.redis_url, decode_responses=True)

async def log_telemetry(tenant_id: str, message: str, trace_id: str, level: str = "INFO", category: str = "GATEWAY"):
    """Pushes a standard telemetry log to Redis as per 0001-telemetry-standard."""
    payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "service_name": "gateway-python",
        "log_level": level,
        "category": category,
        "message": message,
        "context": {}
    }
    r.lpush("telemetry:queue", json.dumps(payload))
