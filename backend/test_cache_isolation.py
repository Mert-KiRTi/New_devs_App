"""
Regression test for the multi-tenant revenue cache leak.

Two tenants own a property with the same ID ('prop-001'). Before the fix the
cache key was 'revenue:{property_id}', so the second tenant received the first
tenant's cached revenue. This test fakes Redis and the revenue calculation so
it runs without a database or Redis server.

Run (inside the backend container):
    docker-compose exec backend python test_cache_isolation.py
"""
import asyncio
import sys
import types
from unittest.mock import MagicMock, patch

# Allow running outside Docker: the real Redis client is replaced by FakeRedis
# below, so if the 'redis' package isn't installed, stub it out for the import.
try:
    import redis.asyncio  # noqa: F401
except ImportError:
    redis_stub = types.ModuleType("redis")
    redis_stub.asyncio = MagicMock()
    sys.modules["redis"] = redis_stub
    sys.modules["redis.asyncio"] = redis_stub.asyncio

import app.services.cache as cache
import app.services.reservations as reservations


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value


async def fake_calculate_total_revenue(property_id, tenant_id):
    totals = {"tenant-a": "2250.000", "tenant-b": "0.00"}
    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": totals[tenant_id],
        "currency": "USD",
        "count": 0,
    }


async def main():
    fake = FakeRedis()
    with patch.object(cache, "redis_client", fake), patch.object(
        reservations, "calculate_total_revenue", fake_calculate_total_revenue
    ):
        # Tenant A populates the cache first, then tenant B requests the same property ID.
        a = await cache.get_revenue_summary("prop-001", "tenant-a")
        b = await cache.get_revenue_summary("prop-001", "tenant-b")
        # Second read for tenant A must come from its own cache entry.
        a_again = await cache.get_revenue_summary("prop-001", "tenant-a")

    assert a["total"] == "2250.000", a
    assert b["total"] == "0.00", f"LEAK! tenant-b received tenant-a's data: {b}"
    assert b["tenant_id"] == "tenant-b", b
    assert a_again == a, a_again
    assert len(fake.store) == 2, list(fake.store.keys())
    print("OK - cache keys:", list(fake.store.keys()))


if __name__ == "__main__":
    asyncio.run(main())
