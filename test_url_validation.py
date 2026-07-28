"""Runnable check for the SSRF allow-list. `python test_url_validation.py` — no framework."""
from utils.url_validation import is_allowed_target, validate_youtube_target
from fastapi import HTTPException

ALLOW = [
    "dQw4w9WgXcQ",                                    # bare video id
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=x",
    "https://m.youtube.com/watch?v=x",
    "lofi hip hop radio",                             # bare search phrase — no host to SSRF
    "https://evil.com@youtube.com/",                 # real host is youtube.com
]
BLOCK = [
    "http://169.254.169.254/latest/meta-data/",      # cloud metadata SSRF
    "https://example.com/evil",
    "ftp://internal/",
    "file:///etc/passwd",
    "https://youtube.com@evil.com/",                 # real host is evil.com
    "https://youtube.com.evil.com/",                 # lookalike host
    "",
]

for v in ALLOW:
    assert is_allowed_target(v), f"should ALLOW: {v!r}"
    assert validate_youtube_target(v) == v

for v in BLOCK:
    assert not is_allowed_target(v), f"should BLOCK: {v!r}"
    try:
        validate_youtube_target(v)
        raise AssertionError(f"expected 400 for {v!r}")
    except HTTPException as e:
        assert e.status_code == 400

print("url_validation: all allow/block cases pass")
