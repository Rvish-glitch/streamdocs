import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import redis
import redis.asyncio as redis_async

from app.core.config import settings


def progress_channel(job_id: uuid.UUID) -> str:
    return f"progress:{job_id}"


def user_events_channel(user_id: uuid.UUID) -> str:
    return f"events:user:{user_id}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis_kwargs() -> dict[str, Any]:
    # Prevent indefinite hangs when Redis is slow/unreachable (common in managed deploys).
    return {
        "decode_responses": True,
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
        "retry_on_timeout": True,
        "health_check_interval": 30,
    }


@lru_cache(maxsize=1)
def get_redis_sync() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, **_redis_kwargs())


@lru_cache(maxsize=1)
def get_redis_async() -> redis_async.Redis:
    return redis_async.Redis.from_url(settings.REDIS_URL, **_redis_kwargs())


def publish_progress_sync(job_id: uuid.UUID, event: dict[str, Any]) -> None:
    payload = json.dumps({**event, "job_id": str(job_id)})
    # Progress updates should never block core processing.
    try:
        get_redis_sync().publish(progress_channel(job_id), payload)
    except Exception:
        # Best-effort only; UI can still poll job status/progress from DB.
        return


def publish_user_event_sync(user_id: uuid.UUID, event: dict[str, Any]) -> None:
    payload = json.dumps({**event, "user_id": str(user_id)})
    try:
        get_redis_sync().publish(user_events_channel(user_id), payload)
    except Exception:
        return
