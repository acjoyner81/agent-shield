import os
import logging
import asyncio
import requests as http_requests
import redis.asyncio as redis
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stripe_sync_worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", "3600"))
USAGE_RECORDS_URL = "https://api.stripe.com/v1/subscription_items/{id}/usage_records"


def _report_usage(sub_item_id: str, quantity: int, timestamp: int) -> None:
    """Report metered usage to Stripe via the usage_records endpoint.

    The installed stripe SDK (>=15) no longer exposes
    `SubscriptionItem.create_usage_record`, so we POST straight to the REST
    endpoint with the secret key instead of depending on an SDK version.
    """
    api_key = os.getenv("STRIPE_SECRET_KEY")
    if not api_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set")

    response = http_requests.post(
        USAGE_RECORDS_URL.format(id=sub_item_id),
        data={
            "quantity": quantity,
            "action": "increment",
            "timestamp": timestamp,
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    response.raise_for_status()


async def sync_usage_to_stripe():
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    current_period = datetime.now(timezone.utc).strftime("%Y-%m")
    pattern = f"billing:usage:*:{current_period}"
    
    logger.info("Starting scheduled Stripe usage synchronization...")
    
    try:
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
            
            for key in keys:
                parts = key.split(":")
                if len(parts) >= 3:
                    tenant_id = parts[2]
                    
                    usage_data = await redis_client.hgetall(key)
                    total_tokens = int(usage_data.get("total_tokens", 0))
                    reported_tokens = int(usage_data.get("reported_tokens", 0))
                    
                    delta_tokens = total_tokens - reported_tokens
                    
                    if delta_tokens > 0:
                        sub_item_id = await redis_client.get(f"tenant:sub_item:{tenant_id}")
                        
                        if sub_item_id:
                            try:
                                _report_usage(
                                    sub_item_id,
                                    quantity=delta_tokens,
                                    timestamp=int(datetime.now(timezone.utc).timestamp())
                                )
                                
                                await redis_client.hset(key, "reported_tokens", total_tokens)
                                logger.info(f"Reported {delta_tokens} tokens for tenant {tenant_id} to Stripe.")
                                
                            except Exception as e:
                                logger.error(f"Failed to report usage for tenant {tenant_id} to Stripe: {e}")
                        else:
                            logger.warning(f"No Stripe subscription item mapping found for tenant {tenant_id}")
                            
            if cursor == 0:
                break
                
    except Exception as e:
        logger.error(f"Error during Stripe usage synchronization loop: {e}")
    finally:
        await redis_client.aclose()
        logger.info("Stripe usage synchronization complete.")

async def run_sync_scheduler():
    while True:
        try:
            await sync_usage_to_stripe()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)

if __name__ == "__main__":
    asyncio.run(run_sync_scheduler())