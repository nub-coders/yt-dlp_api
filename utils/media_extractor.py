import asyncio
import logging
import time

from utils.cookies import cookie_args
from utils.subprocess_limit import subprocess_slot

logger = logging.getLogger("yt_dlp_api.Video_Stream")


async def _extract_video(url: str, cookies: str | None = None):
    """Extract video stream in parallel task"""
    cmd = [
        "yt-dlp",
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
        "-f", "22/18/best[ext=mp4]",  # Optimized: faster format selection
        "--no-playlist",
        "-g",
        url,
    ]

    cmd[1:1] = cookie_args(cookies)

    logger.debug(f"[VIDEO_EXTRACT] Starting...")
    start = time.time()

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

    except asyncio.TimeoutError:
        logger.error(f"[VIDEO_EXTRACT] TIMEOUT after 40s")
        return None
    except Exception as e:
        logger.error(f"[VIDEO_EXTRACT] Exception: {e}")
        return None

    elapsed = round(time.time() - start, 2)

    if process.returncode == 0 and stdout:
        video_url = stdout.decode().strip().split("\n")[0]
        logger.info(f"[VIDEO_EXTRACT] ✅ ({elapsed}s)")
        return video_url

    stderr_text = stderr.decode().strip() if stderr else "no stderr"
    logger.error(f"[VIDEO_EXTRACT] ❌ Failed (exit={process.returncode}, {elapsed}s)")
    logger.error(f"[VIDEO_EXTRACT] stderr: {stderr_text[-300:]}")
    return None


async def _extract_audio(url: str, cookies: str | None = None):
    """Extract audio stream in parallel task"""
    cmd = [
        "yt-dlp",
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
        "-f", "251/250/bestaudio[ext=m4a]/bestaudio",  # Optimized: faster format selection
        "--no-playlist",
        "-g",
        url,
    ]

    cmd[1:1] = cookie_args(cookies)

    logger.debug(f"[AUDIO_EXTRACT] Starting...")
    start = time.time()

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

    except asyncio.TimeoutError:
        logger.error(f"[AUDIO_EXTRACT] TIMEOUT after 40s")
        return None
    except Exception as e:
        logger.error(f"[AUDIO_EXTRACT] Exception: {e}")
        return None

    elapsed = round(time.time() - start, 2)

    if process.returncode == 0 and stdout:
        audio_url = stdout.decode().strip().split("\n")[0]
        logger.info(f"[AUDIO_EXTRACT] ✅ ({elapsed}s)")
        return audio_url

    stderr_text = stderr.decode().strip() if stderr else "no stderr"
    logger.error(f"[AUDIO_EXTRACT] ❌ Failed (exit={process.returncode}, {elapsed}s)")
    logger.error(f"[AUDIO_EXTRACT] stderr: {stderr_text[-300:]}")
    return None


async def resolve_stream_urls(url: str, cookies: str | None = None):
    """Extract video and audio in PARALLEL for 2x speed improvement"""
    logger.info(f"[VIDEO_AUDIO] Extracting in parallel...")
    start = time.time()

    # Launch both extractions concurrently instead of sequentially
    video_task = asyncio.create_task(_extract_video(url, cookies))
    audio_task = asyncio.create_task(_extract_audio(url, cookies))

    # Wait for both to complete
    video_url, audio_url = await asyncio.gather(video_task, audio_task)

    elapsed = round(time.time() - start, 2)

    if video_url and audio_url:
        logger.info(f"[VIDEO_AUDIO] ✅ Both extracted in {elapsed}s (parallel)")
        return video_url, audio_url
    
    logger.error(f"[VIDEO_AUDIO] ❌ Failed - video: {video_url is not None}, audio: {audio_url is not None}")
    return None, None
