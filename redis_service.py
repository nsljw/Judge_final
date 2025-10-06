from datetime import datetime, timedelta
from conf import Settings

import redis.asyncio as redis

r = redis.Redis(host='localhost', port=6379, password="38856", decode_responses=True)

MAX_START_PER_DAY = 3


async def get_minute_limit() -> int:
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    midnight = datetime.combine(tomorrow.date(), datetime.min.time())
    return int((midnight - now).total_seconds() // 60)


async def is_start_limit(user_id: int) -> bool:
    key = f"user:{user_id}:start_count"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, (await get_minute_limit()) * 60)
    return count <= MAX_START_PER_DAY
