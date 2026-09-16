import asyncio
import redis.asyncio as redis

async def check():
    # Try connecting to localhost:6379
    try:
        r = redis.from_url("redis://localhost:6379/0", decode_responses=True)
        # Use a very basic command
        await r.ping()
        print("Ping successful")
        
        # Check keys
        keys = await r.keys("*")
        print(f"Found keys: {keys}")
        
        # Check the specific queue
        llen = await r.llen("telemetry:queue")
        print(f"LLEN telemetry:queue: {llen}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(check())
