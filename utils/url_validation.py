"""Reject non-YouTube targets before they reach a yt-dlp subprocess (SSRF guard).

yt-dlp's generic extractor will fetch ANY http(s) URL, so an unvalidated `q`
turns the stream routes into a "make my server fetch this" primitive. We only
ever serve YouTube, so anything with a non-YouTube host is rejected.
"""
from urllib.parse import urlparse
from fastapi import HTTPException

_ALLOWED_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
})


def is_allowed_target(value: str) -> bool:
    if not value or not value.strip():
        return False
    parsed = urlparse(value.strip())
    if parsed.scheme in ("http", "https"):
        # hostname strips userinfo/port, so "evil.com@youtube.com" -> "evil.com" is blocked
        return parsed.hostname in _ALLOWED_HOSTS
    if parsed.scheme:
        return False  # file:, ftp:, etc. — never allowed
    # No scheme: a bare video id or search phrase. yt-dlp makes no network
    # request for these, so there's nothing to SSRF.
    return True


def validate_youtube_target(value: str) -> str:
    """Return value if it's a YouTube URL / bare id, else raise 400."""
    if not is_allowed_target(value):
        raise HTTPException(status_code=400, detail="Invalid target: only YouTube URLs are allowed")
    return value
