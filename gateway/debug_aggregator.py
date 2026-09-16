import asyncio
import json
import os
import sys
import redis.asyncio as redis

async def debug_redis():
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    print(f"Connecting to Redis at {url}...")
    try:
        r = redis.from_url(url, decode_responses=True)
        print("Connected successfully!")
        
        key = "telemetry:queue"
        length = await r.llen(key)
        print(f"Queue length for {key}: {length}")
        
        if length > 0:
            log = await r.lpop(key)
            print(f"Popped log: {log}")
        else:
            print("Queue is empty.")
            
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(debug_redis())
