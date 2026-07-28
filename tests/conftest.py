import os
os.environ.setdefault("TESTING", "1")

import pytest
import pytest_asyncio
import fakeredis
import fakeredis.aioredis
from httpx import AsyncClient, ASGITransport

# Shared fake Redis server so sync + async clients see the same keyspace
_fake_server = fakeredis.FakeServer()


@pytest.fixture(autouse=True)
def patch_redis(monkeypatch):
    """Replace both Redis clients with fakeredis for every test."""
    import tools
    sync_fake = fakeredis.FakeRedis(server=_fake_server, decode_responses=True)
    async_fake = fakeredis.aioredis.FakeRedis(server=_fake_server, decode_responses=True)

    monkeypatch.setattr(tools, "redis_client", sync_fake)
    monkeypatch.setattr(tools, "_async_redis", async_fake)
    yield
    # flush between tests so state doesn't bleed
    sync_fake.flushall()


@pytest_asyncio.fixture
async def client():
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
