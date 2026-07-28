"""Shared yt-dlp cookie handling.

One helper builds the cookie flags; `bootstrap()` exports browser cookies to
a file at startup and `start_refresh()` re-exports periodically so the file
stays valid as YouTube rotates tokens mid-session.
"""

import logging
import os
import re
import subprocess
import threading
import time

from config import (
    COOKIES_FILE,
    COOKIES_BROWSER,
    COOKIES_BOOTSTRAP_URL,
    COOKIES_REFRESH_HOURS,
)

logger = logging.getLogger("yt_dlp_api.cookies")


def _browsers() -> list[str]:
    """Configured browsers — COOKIES_BROWSER may list several (comma/space-separated)."""
    return [b for b in re.split(r"[,\s]+", COOKIES_BROWSER or "") if b]


def cookie_args(cookies: str | None = None) -> list[str]:
    """yt-dlp cookie flags — prefer the cookies file, fall back to the first browser."""
    path = cookies or COOKIES_FILE
    if path and os.path.exists(path):
        return ["--cookies", path]
    browsers = _browsers()
    return ["--cookies-from-browser", browsers[0]] if browsers else []


def _export() -> None:
    """Re-export the browser cookie jar into COOKIES_FILE.

    yt-dlp writes the cookie jar to `--cookies` after running, so pairing it
    with `--cookies-from-browser` persists the browser session to a file. Each
    configured browser is tried in order until one produces a valid file.
    """
    browsers = _browsers()
    if not browsers:
        logger.warning("[COOKIES] No browser configured (COOKIES_BROWSER); skipping export")
        return
    errors = []
    for browser in browsers:
        cmd = [
            "yt-dlp",
            "--cookies-from-browser", browser,
            "--cookies", COOKIES_FILE,
            "--skip-download",
            COOKIES_BOOTSTRAP_URL,
        ]
        logger.info(f"[COOKIES] Exporting {COOKIES_FILE} from {browser}...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as e:
            errors.append(f"{browser}: {e}")
            continue
        if os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0:
            logger.info(f"[COOKIES] ✅ Wrote {COOKIES_FILE} from {browser}")
            return
        errors.append(f"{browser}: {result.stderr.strip()[-200:]}")
    logger.error(f"[COOKIES] ❌ Export failed for all browsers — {'; '.join(errors)}")


def bootstrap() -> None:
    """Export browser cookies into COOKIES_FILE once, at startup."""
    if os.path.exists(COOKIES_FILE):
        logger.info(f"[COOKIES] Using existing {COOKIES_FILE}")
        return
    _export()


def start_refresh() -> None:
    """Re-export cookies every COOKIES_REFRESH_HOURS in a daemon thread."""
    if COOKIES_REFRESH_HOURS <= 0:
        return

    def _loop():
        while True:
            time.sleep(COOKIES_REFRESH_HOURS * 3600)
            _export()

    threading.Thread(target=_loop, daemon=True).start()
    logger.info(f"[COOKIES] Refresh every {COOKIES_REFRESH_HOURS}h")

