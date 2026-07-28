"""Phase 3 route-contract tests. Run: pytest tests/ -v"""
import os
os.environ.setdefault("TESTING", "1")

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


# ── helpers ────────────────────────────────────────────────────────────────────

async def _make_token(user_id: int = 42) -> str:
    """Seed a valid token into fakeredis and return it."""
    import tools
    token = "testtoken01"
    await tools.set_user_token(user_id, token)
    return token


# ── public endpoints ───────────────────────────────────────────────────────────

async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_root(client):
    r = await client.get("/")
    assert r.status_code == 200


async def test_search_missing_q(client):
    r = await client.get("/search")
    assert r.status_code == 422


# ── auth ───────────────────────────────────────────────────────────────────────

async def test_no_token_returns_401(client):
    r = await client.get("/stream", params={"q": "dQw4w9WgXcQ"})
    assert r.status_code == 401


async def test_bearer_auth_accepted(client):
    token = await _make_token()
    # We don't care about yt-dlp succeeding — just that auth passes (not 401)
    r = await client.get(
        "/stream",
        params={"q": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code != 401


async def test_deprecated_query_param_auth_accepted(client):
    token = await _make_token()
    r = await client.get("/stream", params={"q": "dQw4w9WgXcQ", "token": token})
    assert r.status_code != 401


async def test_invalid_token_returns_401(client):
    r = await client.get(
        "/stream",
        params={"q": "dQw4w9WgXcQ"},
        headers={"Authorization": "Bearer notavalidtoken"},
    )
    assert r.status_code == 401


# ── SSRF allow-list (Phase 1.1 regression) ────────────────────────────────────

async def test_ssrf_blocked(client):
    token = await _make_token()
    r = await client.get(
        "/stream",
        params={"q": "http://169.254.169.254/latest/meta-data/"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


async def test_non_youtube_url_blocked(client):
    token = await _make_token()
    r = await client.get(
        "/info",
        params={"q": "https://example.com/evil"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


# ── rate limiting (Phase 0.1 regression) ──────────────────────────────────────

async def test_rate_limit_enforced(client, monkeypatch):
    """6th request over a limit of 5 must 429."""
    import tools, main
    monkeypatch.setattr(main, "DAILY_LIMIT", 5)
    monkeypatch.setattr(main, "ADMIN_LIMIT", 5)

    token = await _make_token(user_id=99)
    # Seed the counter at 5 (already at limit)
    await tools.set_user_request_count(99, 5)

    r = await client.get(
        "/stream",
        params={"q": "dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 429


# ── rate-limit-status (public) ────────────────────────────────────────────────

async def test_rate_limit_status_no_token(client):
    r = await client.get("/rate-limit-status")
    assert r.status_code == 200


# ── /metrics (Phase 4.3) ──────────────────────────────────────────────────────

async def test_metrics_endpoint(client):
    await client.get("/health")  # generate at least one recorded request
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text

