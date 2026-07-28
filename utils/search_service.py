from urllib.parse import quote_plus, quote
import httpx
import re
import orjson
import asyncio
import os
from utils.bounded_cache import BoundedCache

UPSTASH_REDIS_REST_URL   = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

HEADERS = {
    "User-Agent":      "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
}

YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query={}"
YT_REGEX           = re.compile(r"ytInitialData\s*=\s*(\{.+?\});", re.DOTALL)

# One shared client for the lifetime of the process.
_client = httpx.AsyncClient(http2=True, timeout=15, headers=HEADERS)

# ponytail: stdlib OrderedDict LRU+TTL — avoids cachetools dep; single event loop, no locking needed
MEMORY_CACHE: BoundedCache = BoundedCache(maxsize=5000, ttl=300)
LOCKS:        BoundedCache = BoundedCache(maxsize=5000)

_NORMALIZE_RE = re.compile(r"\s+")


def normalize(q: str) -> str:
    return _NORMALIZE_RE.sub(" ", q.lower().strip())


def format_views(text: str) -> str:
    return text.replace(" views", "").replace(" view", "")


def extract_channel_name(v: dict) -> str:
    for key in ("ownerText", "longBylineText", "shortBylineText"):
        runs = v.get(key, {}).get("runs")
        if runs:
            return runs[0].get("text", "Unknown")
    return "Unknown"


def safe_get(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return {}
        obj = obj.get(key, {})
    return obj


async def fetch_yt_data(url: str):
    r = await _client.get(url)
    m = YT_REGEX.search(r.text)
    return orjson.loads(m.group(1)) if m else None


# ── Upstash Redis helpers ──────────────────────────────────────

async def redis_get(key: str):
    if not UPSTASH_REDIS_REST_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{UPSTASH_REDIS_REST_URL}/get/{quote(key)}",
                headers={"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"},
            )
        if r.status_code == 200:
            return r.json().get("result")
    except Exception:
        pass
    return None


async def redis_set(key: str, value):
    if not UPSTASH_REDIS_REST_URL:
        return
    try:
        if isinstance(value, bytes):
            value = value.decode()
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(
                f"{UPSTASH_REDIS_REST_URL}/set/{quote(key)}",
                headers={"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"},
                json={"value": value},
            )
    except Exception:
        pass


async def close_client():
    await _client.aclose()


# ── Search ─────────────────────────────────────────────────────

async def fetch_results(query: str, limit: int = 1):
    if not query:
        return {"main_results": [], "suggested": []}

    qkey = "search_" + normalize(query)

    # 1. Hot memory cache — no lock needed, just a dict lookup.
    cached = MEMORY_CACHE.get(qkey)
    if cached is not None:
        return cached

    # 2. Upstash cache
    raw = await redis_get(qkey)
    if raw:
        data = orjson.loads(raw.encode())
        MEMORY_CACHE[qkey] = data
        return data

    # 3. Deduplicate concurrent identical queries with a per-key lock.
    lock = LOCKS.setdefault(qkey, asyncio.Lock())
    async with lock:
        # Re-check after acquiring (another coroutine may have populated it).
        cached = MEMORY_CACHE.get(qkey)
        if cached is not None:
            return cached

        try:
            url = YOUTUBE_SEARCH_URL.format(quote_plus(query))
            raw_data = await fetch_yt_data(url)
            if not raw_data:
                return {"main_results": [], "suggested": []}

            contents = safe_get(
                raw_data,
                "contents", "twoColumnSearchResultsRenderer",
                "primaryContents", "sectionListRenderer", "contents",
            )

            results = []
            for section in contents:
                items = safe_get(section, "itemSectionRenderer", "contents")
                for item in items:
                    v = item.get("videoRenderer")
                    if not v:
                        continue
                    video_id = v.get("videoId")
                    if not video_id:
                        continue
                    title_runs = safe_get(v, "title", "runs")
                    title = title_runs[0]["text"] if title_runs else "Unknown"
                    results.append({
                        "title":     title,
                        "video_id":  video_id,
                        "url":       f"https://www.youtube.com/watch?v={video_id}",
                        "duration":  v.get("lengthText", {}).get("simpleText", "LIVE"),
                        "channel":   extract_channel_name(v),
                        "views":     format_views(
                            v.get("viewCountText", {}).get("simpleText", "0 views")
                        ),
                        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    })
                    if len(results) >= limit + 5:
                        break

            output = {
                "main_results": results[:limit],
                "suggested":    results[limit: limit + 5],
            }
            MEMORY_CACHE[qkey] = output
            # Fire-and-forget Redis write — don't await to slow down response.
            asyncio.ensure_future(redis_set(qkey, orjson.dumps(output)))
            return output

        finally:
            LOCKS.pop(qkey, None)


# ── Trending ───────────────────────────────────────────────────

async def fetch_trending(limit: int = 10):
    key = "trending_music_search"
    cached = MEMORY_CACHE.get(key)
    if cached is not None:
        return cached

    raw = await redis_get(key)
    if raw:
        data = orjson.loads(raw.encode())
        MEMORY_CACHE[key] = data
        return data

    data = await fetch_results("music trending india", limit=limit)
    results = (data.get("main_results", []) + data.get("suggested", []))[:limit]

    MEMORY_CACHE[key] = results
    asyncio.ensure_future(redis_set(key, orjson.dumps(results)))
    return results


# ── Suggest ────────────────────────────────────────────────────

async def fetch_suggestions(query: str, limit: int = 10):
    data = await fetch_results(query, limit=limit)
    return data.get("suggested", [])


__all__ = ["fetch_results", "fetch_trending", "fetch_suggestions", "close_client"]
