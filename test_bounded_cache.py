"""Runnable check for BoundedCache. `python test_bounded_cache.py` — no framework."""
import time
from utils.bounded_cache import BoundedCache

# LRU eviction
c = BoundedCache(maxsize=3)
c["a"] = 1; c["b"] = 2; c["c"] = 3
c["a"]          # touch a → b is now LRU
c["d"] = 4      # evicts b
assert "b" not in c, "b should have been evicted"
assert "a" in c and "c" in c and "d" in c

# TTL expiry
c2 = BoundedCache(maxsize=100, ttl=0.05)
c2["x"] = 99
assert c2.get("x") == 99
time.sleep(0.06)
assert c2.get("x") is None, "x should have expired"
assert "x" not in c2

# setdefault
c3 = BoundedCache(maxsize=10)
v = c3.setdefault("k", asyncio_lock := object())
assert v is asyncio_lock
assert c3.setdefault("k", object()) is asyncio_lock  # existing value returned

# __len__
assert len(c3) == 1

print("bounded_cache: all cases pass")
