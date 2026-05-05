import redis.asyncio as aioredis
from core.config import settings

redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

REDIS_TTL = 60 * 60 * 24 * 3
