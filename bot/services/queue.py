from __future__ import annotations

import json
from typing import Any, Dict

from redis.asyncio import Redis

from ..config import get_settings

settings = get_settings()
QUEUE_KEY = "bigbob:events"


async def get_redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def enqueue_event(event: Dict[str, Any]) -> None:
    redis = await get_redis()
    try:
        await redis.rpush(QUEUE_KEY, json.dumps(event))
    finally:
        await redis.close()


async def dequeue_event() -> Dict[str, Any] | None:
    redis = await get_redis()
    try:
        raw = await redis.blpop(QUEUE_KEY, timeout=1)
    finally:
        await redis.close()
    if not raw:
        return None
    _, payload = raw
    return json.loads(payload)