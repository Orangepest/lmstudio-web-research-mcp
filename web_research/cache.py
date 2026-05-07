from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


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

    def stats(self) -> dict[str, int]:
        return {'items': len(self._items), 'max_items': self.max_items, 'ttl_seconds': self.ttl_seconds}


cache = SessionCache()
