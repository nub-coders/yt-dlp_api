import redis.asyncio as aioredis
import redis as sync_redis
import string
import secrets
import time

from config import REDIS_HOST, REDIS_PORT, REDIS_USERNAME, REDIS_PASSWORD, ADMIN_IDS

# ── Redis clients ──────────────────────────────────────────────
# Sync client kept only for admin.py's redis_client usages (keys/info calls).
redis_client = sync_redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
    username=REDIS_USERNAME,
    password=REDIS_PASSWORD,
    socket_connect_timeout=5,
    socket_timeout=5,
)

# Async client — lazy initialized to avoid event loop conflicts
_async_redis = None


async def get_async_redis():
    global _async_redis
    if _async_redis is None:
        _async_redis = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
            username=REDIS_USERNAME,
            password=REDIS_PASSWORD,
            socket_connect_timeout=5,
            socket_timeout=5,
            max_connections=20,
        )
    return _async_redis


def scan_keys(pattern: str = "*", count: int = 500) -> list[str]:
    """Non-blocking replacement for redis_client.keys() — iterates via SCAN so it
    doesn't stall Redis's single thread on a large keyspace."""
    return list(redis_client.scan_iter(match=pattern, count=count))

def generate_token() -> str:
    # secrets, not random — token is a bearer credential
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))


def mask_token(token: str | None) -> str:
    if not token or token == "YOUR_TOKEN":
        return token or "YOUR_TOKEN"
    if len(token) <= 4:
        return "*" * len(token)
    return token[:4] + "*" * (len(token) - 4)


def is_group_chat(chat_or_event) -> bool:
    try:
        from pyrogram.enums import ChatType
        chat = getattr(chat_or_event, "chat", None)
        if not chat and hasattr(chat_or_event, "message"):
            chat = getattr(chat_or_event.message, "chat", None)
        if not chat:
            return False
        return chat.type != ChatType.PRIVATE
    except Exception:
        return False



def is_admin(user_id: int) -> bool:
    return int(user_id) in ADMIN_IDS


# ── Token helpers (all async, use async Redis) ─────────────────

async def get_user_token(user_id) -> str | None:
    redis = await get_async_redis()
    return await redis.get(f"user_token:{user_id}")


async def get_user_token_display(user_id, is_grp: bool, default: str = "") -> str:
    token = await get_user_token(user_id)
    if not token:
        return default
    return mask_token(token) if is_grp else token



async def set_user_token(user_id, token) -> None:
    # Pipeline: two writes in one round-trip
    redis = await get_async_redis()
    async with redis.pipeline(transaction=False) as pipe:
        pipe.set(f"user_token:{user_id}", token)
        pipe.set(f"token_user:{token}", str(user_id))
        await pipe.execute()


async def revoke_user_token(user_id) -> None:
    redis = await get_async_redis()
    old_token = await redis.get(f"user_token:{user_id}")
    async with redis.pipeline(transaction=False) as pipe:
        if old_token:
            pipe.delete(f"token_user:{old_token}")
        pipe.delete(f"user_token:{user_id}")
        await pipe.execute()


async def get_user_by_token(token) -> int | None:
    redis = await get_async_redis()
    user_id = await redis.get(f"token_user:{token}")
    return int(user_id) if user_id else None


async def get_user_request_count(user_id) -> int:
    redis = await get_async_redis()
    val = await redis.get(f"user_requests:{user_id}")
    return int(val) if val else 0


async def set_user_request_count(user_id, count: int) -> None:
    redis = await get_async_redis()
    await redis.setex(f"user_requests:{user_id}", 86400, count)


async def increment_user_requests(user_id) -> int:
    redis = await get_async_redis()
    key = f"user_requests:{user_id}"
    # INCR is atomic; set TTL only on first increment to avoid resetting it
    # mid-day on every request.
    async with redis.pipeline(transaction=False) as pipe:
        pipe.incr(key)
        pipe.ttl(key)
        new_count, ttl = await pipe.execute()
    if ttl < 0:  # key has no TTL yet (first request of the day)
        await redis.expire(key, 86400)
    return new_count


async def increment_failed_requests(user_id, status_code: int, path: str, error_message: str = "") -> int:
    """Track a failed request (4xx/5xx) for a user.

    Stores:
      - user_failed:{user_id}         – per-user daily failure count
      - global_failed_total            – global daily failure count
      - failed_by_status:{status_code} – daily count per HTTP status code
      - failed_by_path:{path}          – daily count per endpoint path
      - failed_log                     – list of last 100 error entries (JSON)
    All counter keys auto-expire after 24 h.
    """
    import json as _json
    import datetime as _dt

    redis = await get_async_redis()
    user_key = f"user_failed:{user_id}"
    global_key = "global_failed_total"
    status_key = f"failed_by_status:{status_code}"
    path_key = f"failed_by_path:{path}"

    # Build a log entry
    log_entry = _json.dumps({
        "ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "user": str(user_id),
        "status": status_code,
        "path": path,
        "error": (error_message or "")[:300],  # cap length
    })

    async with redis.pipeline(transaction=False) as pipe:
        pipe.incr(user_key)
        pipe.ttl(user_key)
        pipe.incr(global_key)
        pipe.ttl(global_key)
        pipe.incr(status_key)
        pipe.ttl(status_key)
        pipe.incr(path_key)
        pipe.ttl(path_key)
        pipe.lpush("failed_log", log_entry)
        pipe.ltrim("failed_log", 0, 99)  # keep last 100
        results = await pipe.execute()

    # Set 24-h TTLs for any newly created keys
    ttl_pairs = [
        (user_key, results[1]),
        (global_key, results[3]),
        (status_key, results[5]),
        (path_key, results[7]),
    ]
    for key, ttl in ttl_pairs:
        if ttl < 0:
            await redis.expire(key, 86400)

    return results[0]  # user's new failed count


async def get_failed_request_count(user_id) -> int:
    redis = await get_async_redis()
    val = await redis.get(f"user_failed:{user_id}")
    return int(val) if val else 0


async def get_recent_errors(count: int = 20) -> list:
    """Return the last `count` error log entries (newest first)."""
    import json as _json
    redis = await get_async_redis()
    raw = await redis.lrange("failed_log", 0, count - 1)
    entries = []
    for item in raw:
        try:
            entries.append(_json.loads(item))
        except Exception:
            continue
    return entries

