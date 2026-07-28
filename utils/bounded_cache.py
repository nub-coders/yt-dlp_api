"""Tiny bounded cache: LRU eviction at maxsize + optional TTL.

Single-event-loop use only (no internal locking) — that's exactly how
MEMORY_CACHE / LOCKS (search_service) and _STREAM_CACHE (main) are used. Exists
to bound three previously-unbounded dicts without adding a cachetools dependency.
"""
import time
from collections import OrderedDict

_MISSING = object()


class BoundedCache:
    def __init__(self, maxsize: int = 1000, ttl: float | None = None):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict = OrderedDict()  # key -> (value, expiry_ts | None)

    def _live(self, key):
        """Return (value) if present and unexpired, else _MISSING (evicting if expired)."""
        item = self._data.get(key)
        if item is None:
            return _MISSING
        value, exp = item
        if exp is not None and exp < time.time():
            del self._data[key]
            return _MISSING
        self._data.move_to_end(key)
        return value

    def __contains__(self, key) -> bool:
        return self._live(key) is not _MISSING

    def get(self, key, default=None):
        v = self._live(key)
        return default if v is _MISSING else v

    def __getitem__(self, key):
        v = self._live(key)
        if v is _MISSING:
            raise KeyError(key)
        return v

    def __setitem__(self, key, value):
        exp = time.time() + self.ttl if self.ttl else None
        self._data[key] = (value, exp)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)  # evict least-recently-used

    def setdefault(self, key, default):
        v = self._live(key)
        if v is _MISSING:
            self[key] = default
            return default
        return v

    def __len__(self) -> int:
        return len(self._data)
