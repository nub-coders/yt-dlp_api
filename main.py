from fastapi import FastAPI, Query, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
import time
import asyncio
import logging
import datetime
import hashlib
import base64
from typing import Optional
import uvicorn
import threading
from pyrogram import Client, idle
import inspect
from fastapi.routing import APIRoute
from fastapi.params import Depends as DependsParam
from pydantic.fields import FieldInfo
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from starlette.routing import Match
from starlette.responses import Response

# Telegram Bot Configuration
from config import API_ID, API_HASH, BOT_TOKEN, GROUP, CHANNEL, DAILY_LIMIT, ADMIN_LIMIT
from bot_commands import setup_bot_commands
from utils.url_validation import validate_youtube_target

# Import shared tools
from tools import (
    redis_client, generate_token, is_admin, get_user_token,
    set_user_token, revoke_user_token, get_user_by_token,
    get_user_request_count, set_user_request_count, increment_user_requests,
    increment_failed_requests
)

# Initialize Pyrogram client with plugins
telegram_app = Client(
    "ytdlp_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN, in_memory=True,
    plugins=dict(root="plugins"),
    device_model="Desktop",
    system_version="Windows 10",
    app_version="3.4.3 x64",
    lang_code="en",
    lang_pack="tdesktop"
)

import os as _os


# ─────────────────────────── FastAPI ───────────────────────────

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bootstrap yt-dlp cookies on API startup so it works under `uvicorn main:app`
    # (any worker/replica), not just `python3 main.py`.
    # ponytail: with --workers>1 in ONE container the workers race on cookies.txt;
    # recommended scaling is WEB_CONCURRENCY=1 + multiple replicas (each writes its
    # own container-local file). Bump to a file lock only if you must run N>1 in one box.
    if not _os.getenv("TESTING"):
        try:
            from utils.logging_config import setup_json_logging
            setup_json_logging()
            from utils.cookies import bootstrap as bootstrap_cookies, start_refresh
            bootstrap_cookies()
            start_refresh()
        except Exception as e:
            logging.error(f"[STARTUP] Cookie bootstrap failed: {e}")
    yield


app = FastAPI(
    title="yt-dlp_api API",
    description="API for yt-dlp-based search, streaming, and playlist extraction with Telegram bot integration",
    lifespan=lifespan,
)

# Rate limiting
FREE_PATHS = frozenset([
    "/", "/search", "/trending", "/suggest", "/health",
    "/rate-limit-status", "/docs", "/openapi.json", "/metrics",
])

_FREE_PREFIXES = (
    "/stream/resolver/",
    "/video-stream/resolver/",
)

# ─────────────────────────── Redirect Stream Storage ───────────────────────────
# Job state lives in Redis (key stream_job:{id} -> JSON {url, mode, extracted_url,
# extracted_time}) so /stream/redirect and /stream/resolver can land on different
# replicas behind a non-sticky load balancer. TTL covers the 45s resolver wait + buffer.
import json as _json
from tools import get_async_redis

_STREAM_TTL = 120


async def _job_get(stream_id: str) -> Optional[dict]:
    redis = await get_async_redis()
    raw = await redis.get(f"stream_job:{stream_id}")
    return _json.loads(raw) if raw else None


def _encode_stream_id(url: str, mode: str) -> str:
    """Generate a stable stream ID from URL + mode"""
    key = f"{mode}:{url}"
    return base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()).decode().rstrip('=')

def _start_background_extraction(stream_id: str, url: str, mode: str):
    """Start background task to extract streaming URL"""
    async def extract():
        try:
            if mode == "video":
                from utils.cache_manager import get_video_stream
                stream_url = await get_video_stream(url)
            else:
                from utils.cache_manager import get_stream
                stream_url = await get_stream(url)

            if stream_url:
                redis = await get_async_redis()
                job = await _job_get(stream_id)
                if job is not None:  # tolerate TTL expiry under load
                    job["extracted_url"] = stream_url
                    job["extracted_time"] = time.time()
                    await redis.set(f"stream_job:{stream_id}", _json.dumps(job), ex=_STREAM_TTL)
                    logging.info(f"[STREAM_RESOLVER] Extracted {mode} URL for {stream_id}")
        except Exception as e:
            logging.error(f"[STREAM_RESOLVER] Failed to extract {mode}: {e}")

    asyncio.create_task(extract())


def _resolve_mode(mode: str) -> str:
    return "video" if mode == "video" else "audio"


async def _ensure_stream_job(url: str, mode: str) -> str:
    validate_youtube_target(url)  # SSRF guard: only YouTube targets reach yt-dlp
    resolved_mode = _resolve_mode(mode)
    stream_id = _encode_stream_id(url, resolved_mode)

    redis = await get_async_redis()
    # SET NX: only the first replica to claim this id starts extraction; others reuse it.
    created = await redis.set(
        f"stream_job:{stream_id}",
        _json.dumps({"url": url, "mode": resolved_mode, "extracted_url": None, "extracted_time": None}),
        ex=_STREAM_TTL,
        nx=True,
    )
    if created:
        _start_background_extraction(stream_id, url, resolved_mode)

    return stream_id


async def _make_temp_redirect(request: Request, url: str, mode: str) -> str:
    stream_id = await _ensure_stream_job(url, mode)
    return str(
        request.url_for("stream_resolver", stream_id=stream_id)
        .replace(scheme="https")
    )


def _token_from_request(request: Request) -> Optional[str]:
    """Prefer `Authorization: Bearer <token>`; fall back to the deprecated ?token= query param."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.query_params.get("token")


async def get_current_user(token: Optional[str] = Query(None)):
    """Get current user from token"""
    if not token:
        return None
    try:
        user_id = await get_user_by_token(token)
        return user_id
    except:
        return None


async def require_token(request: Request):
    """Require valid token (header or ?token=) for protected endpoints"""
    user_id = await get_current_user(_token_from_request(request))
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Token required",
                "message": "Get your token from the Telegram bot via /start and send it as 'Authorization: Bearer <token>' (or the deprecated ?token= query param)"
            }
        )
    return user_id



class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in FREE_PATHS or any(
            request.url.path.startswith(prefix) for prefix in _FREE_PREFIXES
        ):
            return await call_next(request)

        token   = _token_from_request(request)
        user_id = await get_current_user(token)

        if not user_id:
            required_args, optional_args = get_arguments_for_request(request)
            return JSONResponse(
                status_code=401,
                content=jsonable_encoder({
                    "error":   "Token required",
                    "message": "Get your token from @ytdlp_nub_bot using /start",
                    "required_arguments": required_args,
                    "optional_arguments": optional_args,
                }),
            )

        user_limit = ADMIN_LIMIT if is_admin(user_id) else DAILY_LIMIT

        # --- single Redis round-trip: check + increment atomically ---
        # We increment optimistically; if over limit we return 429.
        # This avoids a separate GET before the INCR.
        new_count = await increment_user_requests(user_id)

        if new_count > user_limit:
            return JSONResponse(
                status_code=429,
                content={
                    "error":              "Daily limit exceeded",
                    "message":            f"Limit: {user_limit} req/day. Search is always free.",
                    "remaining_requests": 0,
                    "reset_time":         "Resets at midnight UTC",
                },
            )

        response = await call_next(request)

        # Log failed requests (4xx / 5xx) with the error message
        if response.status_code >= 400:
            try:
                # Read the streaming body so we can inspect it
                body_chunks = []
                async for chunk in response.body_iterator:
                    body_chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
                body_bytes = b"".join(body_chunks)

                # Try to extract the error message from JSON
                error_msg = ""
                try:
                    import json as _json
                    payload = _json.loads(body_bytes)
                    error_msg = payload.get("error", "") or payload.get("detail", "") or payload.get("message", "")
                    if isinstance(error_msg, dict):
                        error_msg = error_msg.get("error", "") or error_msg.get("message", "") or str(error_msg)
                except Exception:
                    error_msg = body_bytes[:300].decode(errors="replace")

                await increment_failed_requests(
                    user_id,
                    status_code=response.status_code,
                    path=request.url.path,
                    error_message=str(error_msg),
                )

                # Rebuild the response since we consumed the body iterator
                from starlette.responses import Response as StarletteResponse
                response = StarletteResponse(
                    content=body_bytes,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            except Exception:
                pass  # never block a response for logging

        remaining = max(0, user_limit - new_count)
        reset_ts  = int(
            datetime.datetime.combine(
                datetime.date.today() + datetime.timedelta(days=1),
                datetime.time.min,
            ).timestamp()
        )
        response.headers["X-RateLimit-Limit"]     = str(user_limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"]     = str(reset_ts)

        return response


def clean_type_name(annotation) -> str:
    if annotation == inspect.Parameter.empty:
        return "any"
    
    # Handle typing wrappers like Optional, Union, etc.
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        args = getattr(annotation, "__args__", [])
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            return clean_type_name(non_none_args[0])
        elif len(non_none_args) > 1:
            return " | ".join(clean_type_name(arg) for arg in non_none_args)
            
    name = getattr(annotation, "__name__", str(annotation))
    if name == "str":
        return "string"
    if name == "int":
        return "integer"
    if name == "bool":
        return "boolean"
    if name == "float":
        return "number"
    return name


def get_endpoint_args(route: APIRoute):
    required_args = {}
    optional_args = {}
    
    sig = inspect.signature(route.endpoint)
    for name, param in sig.parameters.items():
        # Skip internal parameter types like Request or Response
        if param.annotation in (Request, Response) or name in ("request", "response"):
            continue
        # Skip dependencies
        if isinstance(param.default, DependsParam):
            continue
            
        param_type = clean_type_name(param.annotation)
        description = ""
        param_in = "query"
        
        # Check if it's a path parameter
        if f"{{{name}}}" in route.path:
            param_in = "path"
            
        if isinstance(param.default, FieldInfo):
            is_req = param.default.is_required()
            default_val = param.default.default
            # Handle PydanticUndefined default value
            if default_val == ... or default_val.__class__.__name__ == "PydanticUndefined":
                default_val = None
            description = param.default.description or ""
            
            # Determine location from FieldInfo type
            from fastapi.params import Query, Path, Header, Cookie, Body
            if isinstance(param.default, Path):
                param_in = "path"
            elif isinstance(param.default, Query):
                param_in = "query"
            elif isinstance(param.default, Header):
                param_in = "header"
            elif isinstance(param.default, Cookie):
                param_in = "cookie"
            elif isinstance(param.default, Body):
                param_in = "body"
        else:
            is_req = (param.default == inspect.Parameter.empty)
            default_val = None if is_req else param.default

        info = {
            "type": param_type,
            "in": param_in,
        }
        if description:
            info["description"] = description
            
        if is_req:
            required_args[name] = info
        else:
            info["default"] = default_val
            optional_args[name] = info
            
    return required_args, optional_args


def get_arguments_for_request(request: Request):
    required_args = {}
    optional_args = {}
    
    route = request.scope.get("route")
    if not route:
        for r in request.app.routes:
            match, _ = r.matches(request.scope)
            if match == Match.FULL:
                route = r
                break
                
    if route and isinstance(route, APIRoute):
        required_args, optional_args = get_endpoint_args(route)
        
    return required_args, optional_args


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    required_args, optional_args = get_arguments_for_request(request)
    
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({
            "error": "Validation Error",
            "message": "The endpoint was used incorrectly. Please verify the arguments below.",
            "details": exc.errors(),
            "endpoint": request.url.path,
            "required_arguments": required_args,
            "optional_arguments": optional_args
        })
    )


app.add_middleware(RateLimitMiddleware)


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    from utils import metrics
    start = time.time()
    response = await call_next(request)
    # Use the route template (not the raw path) so per-id URLs don't explode cardinality
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    metrics.record(request.method, path, response.status_code, time.time() - start)
    return response


@app.get("/metrics")
async def metrics_endpoint():
    from utils import metrics
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")


# ─────────────────────────── Endpoints ───────────────────────────

@app.get("/")
async def read_root():
    """API welcome page"""
    return {
        "name": "yt-dlp_api API",
        "version": "2026.3.12",
        "endpoints": {
            "/search": "Search songs via scrape or YouTube Data API (FREE)",
            "/trending": "Get trending music (FREE)",
            "/suggest": "Get song suggestions for a query (FREE)",
            "/stream": "Get audio or video stream URL (token required)",
            "/stream/redirect": "Get instant redirect URL for pytgcall (token required)",
                "/video-stream": "Get separate video + audio URLs (token required)",
            "/video-stream/redirect": "Get instant redirect URL for video/audio (token required)",
            "/info": "Search + stream URL in one call (token required)",
            "/playlist": "Get all songs from a YouTube playlist (token required)",
            "/health": "Health check (FREE)",
            "/rate-limit-status": "Check your rate limit usage",
        },
        "free_endpoints": ["/search", "/trending", "/suggest", "/health"],
        "auth": "Get your token from the Telegram bot @ytdlp_nub_bot using /start",
        "redirect_note": "Use /stream/redirect and /video-stream/redirect with pytgcall for instant response + background extraction"
    }


@app.get("/search")
async def search_songs(
    q: str = Query(..., description="Search query"),
    limit: int = Query(5, description="Number of results", ge=1, le=20),
    method: str = Query("scrape", description="Search method: 'scrape' (free) or 'api' (uses YouTube Data API)")
):
    """Search YouTube for songs — FREE (no token required)"""
    start_time = time.time()

    try:
        if method == "api":
            from utils.youtube_api import fetch_results
            results = await fetch_results(q, limit=limit)
            elapsed = round(time.time() - start_time, 2)
            return JSONResponse(content={
                "query": q,
                "method": "youtube_data_api",
                "results": results,
                "total_results": len(results),
                "time_taken": f"{elapsed} sec"
            })
        else:
            from utils.search_service import fetch_results
            data = await fetch_results(q, limit=limit)
            elapsed = round(time.time() - start_time, 2)
            return JSONResponse(content={
                "query": q,
                "method": "scrape",
                "results": data.get("main_results", []),
                "suggested": data.get("suggested", []),
                "total_results": len(data.get("main_results", [])),
                "time_taken": f"{elapsed} sec"
            })

    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return JSONResponse(
            content={"error": str(e), "time_taken": f"{elapsed} sec"},
            status_code=400
        )


@app.get("/video-stream/redirect")
async def video_stream_redirect(
    request: Request,
    q: str = Query(..., description="YouTube video URL"),
    type: str = Query("audio", description="Which stream: 'video' (mp4) or 'audio' (m4a)"),
    token: str = Query(..., description="Your API token"),
    user_id: int = Depends(require_token)
):
    """Get instant redirect URL for video or audio stream (pytgcall friendly!)."""
    stream_id = await _ensure_stream_job(q, "video" if type == "video" else "audio")

    # Return 307 Temporary Redirect to resolver
    return RedirectResponse(
        url=str(
            request.url_for("video_stream_resolver", stream_id=stream_id)
            .include_query_params(token=token)
            .replace(scheme="https")
        ),
        status_code=307,
    )


@app.get("/video-stream/resolver/{stream_id}")
async def video_stream_resolver(stream_id: str):
    """Resolver endpoint for video stream redirect."""
    job = await _job_get(stream_id)
    if job is None:
        return JSONResponse(
            content={"error": "Stream not found", "hint": "Use /video-stream/redirect to get a valid URL"},
            status_code=404
        )

    # If not extracted yet, poll Redis up to 45s for the background job to finish
    if job["extracted_url"] is None:
        logging.info(f"[VIDEO_STREAM_RESOLVER] Waiting for extraction... {stream_id}")
        for i in range(45):  # 45 second timeout
            await asyncio.sleep(1)
            job = await _job_get(stream_id)
            if job is None or job["extracted_url"] is not None:
                break

    if job and job["extracted_url"]:
        logging.info(f"[VIDEO_STREAM_RESOLVER] Redirecting to stream URL {stream_id}")
        return RedirectResponse(
            url=job["extracted_url"],
            status_code=307
        )
    else:
        return JSONResponse(
            content={"error": "Failed to extract stream URL", "url": job["url"] if job else None},
            status_code=500
        )


@app.get("/stream/redirect")
async def stream_redirect(
    request: Request,
    q: str = Query(..., description="YouTube video URL"),
    mode: str = Query("audio", description="Mode: 'audio' or 'video'"),
    token: str = Query(..., description="Your API token"),
    user_id: int = Depends(require_token)
):
    """Get instant redirect URL for streaming (pytgcall friendly!)."""
    stream_id = await _ensure_stream_job(q, mode)

    # Return 307 Temporary Redirect to resolver
    return RedirectResponse(
        url=str(
            request.url_for("stream_resolver", stream_id=stream_id)
            .include_query_params(token=token)
            .replace(scheme="https")
        ),
        status_code=307,
    )


@app.get("/stream/resolver/{stream_id}")
async def stream_resolver(stream_id: str):
    """Resolver endpoint for redirect streaming."""
    job = await _job_get(stream_id)
    if job is None:
        return JSONResponse(
            content={"error": "Stream not found", "hint": "Use /stream/redirect to get a valid URL"},
            status_code=404
        )

    # If not extracted yet, poll Redis up to 45s for the background job to finish
    if job["extracted_url"] is None:
        logging.info(f"[STREAM_RESOLVER] Waiting for extraction... {stream_id}")
        for i in range(45):  # 45 second timeout
            await asyncio.sleep(1)
            job = await _job_get(stream_id)
            if job is None or job["extracted_url"] is not None:
                break

    if job and job["extracted_url"]:
        logging.info(f"[STREAM_RESOLVER] Redirecting to stream URL {stream_id}")
        return RedirectResponse(
            url=job["extracted_url"],
            status_code=307
        )
    else:
        return JSONResponse(
            content={"error": "Failed to extract stream URL", "url": job["url"] if job else None},
            status_code=500
        )


@app.get("/stream")
async def get_stream_url(
    request: Request,
    q: str = Query(..., description="YouTube video URL"),
    mode: str = Query("audio", description="Mode: 'audio' or 'video'"),
    redirect: bool = Query(False, description="Return a temporary redirect URL instead of the final stream URL"),
    token: Optional[str] = Query(None, description="API token (deprecated — prefer 'Authorization: Bearer <token>')"),
    user_id: int = Depends(require_token)
):
    """Get stream URL for a YouTube video.

    Modes:
    - `audio`: Best audio stream (m4a)
    - `video`: Best combined video+audio (mp4, 360p)
    """
    validate_youtube_target(q)  # SSRF guard
    start_time = time.time()

    try:
        if redirect:
            elapsed = round(time.time() - start_time, 2)
            return JSONResponse(content={
                "url": q,
                "mode": mode,
                "redirect_url": await _make_temp_redirect(request, q, mode),
                "stream_url": None,
                "time_taken": f"{elapsed} sec"
            })

        if mode == "video":
            from utils.cache_manager import get_video_stream
            stream_url = await get_video_stream(q)
        else:
            from utils.cache_manager import get_stream
            stream_url = await get_stream(q)

        elapsed = round(time.time() - start_time, 2)

        if stream_url:
            return JSONResponse(content={
                "url": q,
                "mode": mode,
                "stream_url": stream_url,
                "time_taken": f"{elapsed} sec"
            })
        else:
            return JSONResponse(
                content={"error": "Failed to extract stream URL", "time_taken": f"{elapsed} sec"},
                status_code=500
            )

    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return JSONResponse(
            content={"error": str(e), "time_taken": f"{elapsed} sec"},
            status_code=400
        )


@app.get("/video-stream")
async def video_stream_urls(
    request: Request,
    q: str = Query(..., description="YouTube video URL"),
    redirect: bool = Query(False, description="Return a temporary redirect URL instead of separate stream URLs"),
    token: Optional[str] = Query(None, description="API token (deprecated — prefer 'Authorization: Bearer <token>')"),
    user_id: int = Depends(require_token)
):
    """Get separate best-quality video and audio URLs.

    Returns two URLs: bestvideo (mp4) + bestaudio (m4a).
    Use these with ffmpeg or a player that supports dual-source playback.
    """
    validate_youtube_target(q)  # SSRF guard
    start_time = time.time()

    try:
        if redirect:
            elapsed = round(time.time() - start_time, 2)
            return JSONResponse(content={
                "url": q,
                "redirect_url": await _make_temp_redirect(request, q, "video"),
                "stream_url": None,
                "time_taken": f"{elapsed} sec"
            })

        from utils.media_extractor import resolve_stream_urls
        video_url, audio_url = await resolve_stream_urls(q)
        elapsed = round(time.time() - start_time, 2)

        if video_url and audio_url:
            return JSONResponse(content={
                "url": q,
                "video_url": video_url,
                "audio_url": audio_url,
                "time_taken": f"{elapsed} sec"
            })
        else:
            return JSONResponse(
                content={"error": "Failed to extract video+audio URLs", "time_taken": f"{elapsed} sec"},
                status_code=500
            )

    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return JSONResponse(
            content={"error": str(e), "time_taken": f"{elapsed} sec"},
            status_code=400
        )


@app.get("/info")
async def video_info(
    request: Request,
    q: str = Query(..., description="YouTube video URL or search query"),
    max_results: int = Query(1, description="Max results for search queries", ge=1, le=10),
    mode: str = Query("audio", description="Mode: 'audio' for audio-only, 'video' for video stream"),
    redirect: bool = Query(True, description="Return a temporary redirect URL instead of waiting for the final stream"),
    token: Optional[str] = Query(None, description="API token (deprecated — prefer 'Authorization: Bearer <token>')"),
    user_id: int = Depends(require_token)
):
    """Get video info + stream URL (token required)"""
    validate_youtube_target(q)  # SSRF guard (bare search phrases pass — no host to SSRF)
    start_time = time.time()

    def extract_video_id_from_url(value: str) -> str | None:
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(value)
        if "youtu.be" in parsed.netloc:
            candidate = parsed.path.strip("/")
            return candidate or None

        if "youtube.com" in parsed.netloc:
            query_id = parse_qs(parsed.query).get("v", [None])[0]
            if query_id:
                return query_id

        return None

    try:
        # Check if it's a YouTube URL
        import re
        yt_url_pattern = re.compile(r'(youtube\.com|youtu\.be)')
        is_url = bool(yt_url_pattern.search(q))

        if is_url:
            # Direct URL — get stream and info concurrently
            from utils.youtube_api import GetVideoById

            video_id = extract_video_id_from_url(q)
            metadata_task = asyncio.create_task(GetVideoById(video_id)) if video_id else None

            if redirect:
                temp_redirect_url = await _make_temp_redirect(request, q, mode)
                metadata_result = await metadata_task if metadata_task else None
                info = metadata_result if isinstance(metadata_result, dict) else {}

                elapsed = round(time.time() - start_time, 2)
                return JSONResponse(content={
                    "query_type": "url",
                    "title": info.get("title"),
                    "duration": info.get("duration"),
                    "youtube_link": q,
                    "channel_name": info.get("channel_name") or info.get("channel") or info.get("artist_name"),
                    "views": info.get("views"),
                    "video_id": info.get("video_id"),
                    "stream_url": temp_redirect_url,
                    "thumbnail": info.get("thumbnail"),
                    "time_taken": f"{elapsed} sec"
                })

            metadata_result = await metadata_task if metadata_task else None
            info = metadata_result if isinstance(metadata_result, dict) else {}

            elapsed = round(time.time() - start_time, 2)
            return JSONResponse(content={
                "query_type": "url",
                "title": info.get("title"),
                "duration": info.get("duration"),
                "youtube_link": q,
                "channel_name": info.get("channel_name") or info.get("channel") or info.get("artist_name"),
                "views": info.get("views"),
                "video_id": info.get("video_id"),
                "stream_url": await _make_temp_redirect(request, q, mode),
                "thumbnail": info.get("thumbnail"),
                "time_taken": f"{elapsed} sec"
            })
        else:
            # Search query
            from utils.search_service import fetch_results
            search_data = await fetch_results(q, limit=max_results)

            if max_results == 1 and search_data.get("main_results"):
                # Single result — also get stream URL
                result = search_data["main_results"][0]
                video_url = result.get("url", "")

                elapsed = round(time.time() - start_time, 2)
                return JSONResponse(content={
                    "query_type": "search",
                    "query": q,
                    "title": result.get("title"),
                    "duration": result.get("duration"),
                    "youtube_link": result.get("url"),
                    "channel_name": result.get("channel"),
                    "views": result.get("views"),
                    "video_id": result.get("video_id"),
                    "stream_url": await _make_temp_redirect(request, video_url, mode),
                    "thumbnail": result.get("thumbnail"),
                    "time_taken": f"{elapsed} sec"
                })
            else:
                # Multiple results — return list only
                elapsed = round(time.time() - start_time, 2)
                results = search_data.get("main_results", [])
                return JSONResponse(content={
                    "query_type": "search",
                    "query": q,
                    "results": results,
                    "total_results": len(results),
                    "time_taken": f"{elapsed} sec"
                })

    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return JSONResponse(
            content={"error": str(e), "time_taken": f"{elapsed} sec"},
            status_code=400
        )


@app.get("/trending")
async def trending_songs(
    limit: int = Query(10, description="Number of trending songs", ge=1, le=20)
):
    """Get trending songs — FREE (no token required)"""
    start_time = time.time()

    try:
        from utils.search_service import fetch_trending
        results = await fetch_trending(limit=limit)
        elapsed = round(time.time() - start_time, 2)

        return JSONResponse(content={
            "results": results,
            "total_results": len(results),
            "time_taken": f"{elapsed} sec"
        })

    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return JSONResponse(
            content={"error": str(e), "time_taken": f"{elapsed} sec"},
            status_code=400
        )


@app.get("/suggest")
async def suggest_songs(
    q: str = Query(..., description="Search query"),
    limit: int = Query(5, description="Number of suggestions", ge=1, le=20)
):
    """Get song suggestions — FREE (no token required)"""
    start_time = time.time()

    try:
        from utils.search_service import fetch_suggestions
        results = await fetch_suggestions(q, limit=limit)
        elapsed = round(time.time() - start_time, 2)

        return JSONResponse(content={
            "query": q,
            "results": results,
            "total_results": len(results),
            "time_taken": f"{elapsed} sec"
        })

    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return JSONResponse(
            content={"error": str(e), "time_taken": f"{elapsed} sec"},
            status_code=400
        )


@app.get("/playlist")
async def playlist_songs(
    url: str = Query(..., description="YouTube playlist URL or playlist ID (e.g. PLxxxxxxx, RDxxxxxx)"),
    token: Optional[str] = Query(None, description="API token (deprecated — prefer 'Authorization: Bearer <token>')"),
    user_id: int = Depends(require_token)
):
    """Get all songs from a YouTube playlist.

    Supports normal playlists (PL...), auto-generated playlists (OL..., UU...),
    and YouTube Mix playlists (RD...).
    """
    start_time = time.time()

    try:
        from utils.playlist_parser import extract_playlist
        songs = await extract_playlist(url)
        elapsed = round(time.time() - start_time, 2)

        return JSONResponse(content={
            "playlist_url": url,
            "songs": songs,
            "total_songs": len(songs),
            "time_taken": f"{elapsed} sec"
        })

    except ValueError as e:
        elapsed = round(time.time() - start_time, 2)
        return JSONResponse(
            content={"error": str(e), "time_taken": f"{elapsed} sec"},
            status_code=400
        )
    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return JSONResponse(
            content={"error": str(e), "time_taken": f"{elapsed} sec"},
            status_code=500
        )


# `/version` endpoint removed — startup info helper removed from source-only repo


@app.get("/health")
async def health_check():
    """Quick health check endpoint"""
    return {"status": "ok"}


@app.get("/rate-limit-status")
async def rate_limit_status(token: Optional[str] = Query(None, description="Your API token")):
    """Check current rate limit status"""
    user_id = await get_current_user(token)
    if user_id:
        used = await get_user_request_count(user_id)
        limit = ADMIN_LIMIT if is_admin(user_id) else DAILY_LIMIT

        return {
            "user_id": user_id,
            "daily_limit": limit,
            "requests_used": used,
            "requests_remaining": max(0, limit - used),
            "reset_time": "Resets at midnight UTC",
            "is_admin": is_admin(user_id),
            "auth_method": "token"
        }
    else:
        return {
            "error": "No token provided",
            "message": "Please get your token from the Telegram bot using /start command and add it as ?token=YOUR_TOKEN",
            "auth_method": "none"
        }


# ─────────────────────────── Run Services ───────────────────────────

def start_services():
    print("🌐 Starting FastAPI server on http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info", loop="asyncio")


if __name__ == "__main__":
    # All-in-one: FastAPI (with cookie bootstrap via lifespan) + Telegram bot in one
    # process. For horizontal scaling run the API and bot separately instead —
    # `uvicorn main:app --workers N` (or N replicas) + `python3 bot.py`.
    try:
        threading.Thread(target=start_services, daemon=True).start()
        try:
            telegram_app.start()
            setup_bot_commands(telegram_app)
            me = telegram_app.me
            print(f"🤖 Bot Started: @{me.username} ({me.first_name}) [ID: {me.id}]")
            idle()
            telegram_app.stop()
        except Exception as e:
            print(f"Bot failed: {e}, API still running...")
            import time as _time
            while True:
                _time.sleep(3600)
    except KeyboardInterrupt:
        print("Services stopped by user")
    except Exception as e:
        print(f"Error starting services: {e}")
