"""Redis-backed rate limiting boundary."""


async def check_rate_limit(tenant_id: str, limit_per_minute: int) -> bool:
    """Placeholder for an atomic Redis token-bucket implementation."""
    del tenant_id, limit_per_minute
    return True
