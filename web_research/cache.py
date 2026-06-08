from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from web_research.config import settings


@dataclass
class SessionCache:
    ttl_seconds: int = 3600
    max_items: int = 256
    _items: dict[str, tuple[float, Any]] = field(default_factory=dict)

    def get(self, key: str) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        created_at, value = item
        if time.time() - created_at > self.ttl_seconds:
            self._items.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._items) >= self.max_items:
            oldest = min(self._items, key=lambda item: self._items[item][0])
            self._items.pop(oldest, None)
        self._items[key] = (time.time(), value)

    def invalidate_older_than(self, max_age_seconds: int) -> int:
        cutoff = time.time() - max_age_seconds
        keys = [key for key, (created_at, _value) in self._items.items() if created_at < cutoff]
        for key in keys:
            self._items.pop(key, None)
        return len(keys)

    def invalidate_content_hash(self, content_hash: str) -> int:
        if not content_hash:
            return 0
        keys = []
        for key, (_created_at, value) in self._items.items():
            if isinstance(value, dict) and value.get('content_hash') == content_hash:
                keys.append(key)
                continue
            if isinstance(value, dict) and isinstance(value.get('snapshot'), dict):
                if value['snapshot'].get('content_hash') == content_hash:
                    keys.append(key)
        for key in keys:
            self._items.pop(key, None)
        return len(keys)

    def clear(self) -> int:
        count = len(self._items)
        self._items.clear()
        return count

    def stats(self) -> dict[str, int]:
        expired = sum(1 for created_at, _value in self._items.values() if time.time() - created_at > self.ttl_seconds)
        return {
            'items': len(self._items),
            'max_items': self.max_items,
            'ttl_seconds': self.ttl_seconds,
            'expired_items': expired,
        }


cache = SessionCache(ttl_seconds=settings.cache_ttl_seconds, max_items=settings.cache_max_items)
