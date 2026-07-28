import asyncio
import os
import hashlib
import json
import time
import logging
from urllib.parse import urlparse, parse_qs

from utils.cookies import cookie_args
from utils.subprocess_limit import subprocess_slot

__all__ = ["get_stream", "get_video_stream"]

logger = logging.getLogger("yt_dlp_api.Stream")

_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
os.makedirs(_CACHE_DIR, exist_ok=True)

_MEM_CACHE = {}
_REDIS_CLIENT = None  # Lazy initialized

async def _get_redis():
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        try:
            from tools import get_async_redis
            _REDIS_CLIENT = await get_async_redis()
        except Exception:
            _REDIS_CLIENT = False  # Disable if unavailable
    return _REDIS_CLIENT if _REDIS_CLIENT else None


def _key(url: str, prefix: str = "") -> str:
    return hashlib.md5((prefix + url).encode()).hexdigest()


def _cache_path(url: str, prefix: str = "") -> str:
    return os.path.join(_CACHE_DIR, _key(url, prefix) + ".json")


def _extract_expire(stream_url: str) -> int | None:
    try:
        q = parse_qs(urlparse(stream_url).query)
        expire = int(q.get("expire", [0])[0])
        return expire if expire > int(time.time()) else None
    except Exception:
        return None


def _read_cache(url: str, prefix: str = "") -> tuple[str | None, int]:
    """Read cache and return (url, remaining_ttl). TTL -1 means expired."""
    path = _cache_path(url, prefix)

    if not os.path.exists(path):
        return None, -1

    try:
        with open(path, "r") as f:
            data = json.load(f)

        expire = data.get("expire", 0)
        remaining = expire - time.time()

        if remaining > 15:  # Only return if 15s buffer remains
            logger.debug(f"[CACHE HIT] {prefix}{url[:80]}... (expires in {int(remaining)}s)")
            return data.get("url"), int(remaining)

        logger.debug(f"[CACHE EXPIRED] {prefix}{url[:80]}... removing")
        try:
            os.remove(path)
        except Exception:
            pass

    except Exception as e:
        logger.debug(f"[CACHE READ ERROR] {e}")
        try:
            os.remove(path)
        except Exception:
            pass

    return None, -1


def _write_cache(url: str, stream_url: str, prefix: str = ""):
    expire = _extract_expire(stream_url)
    if not expire:
        logger.warning(f"[CACHE SKIP] No expire found in stream URL for {url[:80]}")
        return

    try:
        with open(_cache_path(url, prefix), "w") as f:
            json.dump(
                {
                    "url": stream_url,
                    "expire": expire,
                },
                f,
            )
        logger.info(f"[CACHE WRITE] {prefix}{url[:80]}... (expires in {int(expire - time.time())}s)")
    except Exception as e:
        logger.error(f"[CACHE WRITE ERROR] {e}")


async def _run_yt_dlp(url: str, format_selector: str, cookies: str | None):
    cmd = [
        "yt-dlp",
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
        "-f", format_selector,
        "--no-playlist",
        "-g",
        url,
    ]

    # Use cookies file if present, otherwise fall back to browser cookies
    cmd[1:1] = cookie_args(cookies)

    logger.info(f"[YT-DLP] Running: {' '.join(cmd)}")
    try:
        async with subprocess_slot:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=40,
            )
        if process.returncode == 0 and stdout:
            return stdout.decode().strip().split("\n")[0]
        else:
            err = stderr.decode().strip() if stderr else "unknown error"
            logger.error(f"[YT-DLP] Failed: {err}")
            return None
    except Exception as e:
        logger.error(f"[YT-DLP] Exception: {e}")
        return None


async def get_stream(url: str, cookies: str | None = None) -> str | None:
    prefix = "audio:"
    
    # 1. Read from Redis cache (if redis available)
    redis = await _get_redis()
    if redis:
        try:
            cached_url = await redis.get(f"cache:{prefix}{url}")
            if cached_url:
                logger.info(f"[REDIS CACHE HIT] {prefix}{url[:80]}...")
                return cached_url
        except Exception as e:
            logger.error(f"[REDIS GET ERROR] {e}")

    # 2. Read from local file cache
    cached_url, remaining_ttl = _read_cache(url, prefix)
    if cached_url:
        # Re-populate Redis if we missed it
        if redis:
            try:
                await redis.setex(f"cache:{prefix}{url}", remaining_ttl, cached_url)
            except Exception:
                pass
        return cached_url

    # 3. Resolve using yt-dlp
    stream_url = await _run_yt_dlp(url, "251/250/bestaudio[ext=m4a]/bestaudio", cookies)
    if stream_url:
        # 4. Write cache
        _write_cache(url, stream_url, prefix)
        expire = _extract_expire(stream_url)
        if expire and redis:
            ttl = int(expire - time.time())
            if ttl > 0:
                try:
                    await redis.setex(f"cache:{prefix}{url}", ttl, stream_url)
                except Exception:
                    pass
    return stream_url


async def get_video_stream(url: str, cookies: str | None = None) -> str | None:
    prefix = "video:"
    
    # 1. Read from Redis cache
    redis = await _get_redis()
    if redis:
        try:
            cached_url = await redis.get(f"cache:{prefix}{url}")
            if cached_url:
                logger.info(f"[REDIS CACHE HIT] {prefix}{url[:80]}...")
                return cached_url
        except Exception as e:
            logger.error(f"[REDIS GET ERROR] {e}")

    # 2. Read from local file cache
    cached_url, remaining_ttl = _read_cache(url, prefix)
    if cached_url:
        if redis:
            try:
                await redis.setex(f"cache:{prefix}{url}", remaining_ttl, cached_url)
            except Exception:
                pass
        return cached_url

    # 3. Resolve using yt-dlp
    stream_url = await _run_yt_dlp(url, "22/18/best[ext=mp4]", cookies)
    if stream_url:
        # 4. Write cache
        _write_cache(url, stream_url, prefix)
        expire = _extract_expire(stream_url)
        if expire and redis:
            ttl = int(expire - time.time())
            if ttl > 0:
                try:
                    await redis.setex(f"cache:{prefix}{url}", ttl, stream_url)
                except Exception:
                    pass
    return stream_url

