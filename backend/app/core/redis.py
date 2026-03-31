import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis
import redis.asyncio as redis_async

from app.core.config import settings


def progress_channel(job_id: uuid.UUID) -> str:
    return f"progress:{job_id}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_redis_sync() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def get_redis_async() -> redis_async.Redis:
    return redis_async.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def publish_progress_sync(job_id: uuid.UUID, event: dict[str, Any]) -> None:
    payload = json.dumps({**event, "job_id": str(job_id)})
    get_redis_sync().publish(progress_channel(job_id), payload)
