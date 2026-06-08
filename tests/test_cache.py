from __future__ import annotations

import time
import unittest

from web_research.cache import SessionCache


class SessionCacheTests(unittest.TestCase):
    def test_get_expires_items_by_ttl(self) -> None:
        cache = SessionCache(ttl_seconds=1)
        cache.set('a', {'ok': True})
        created_at, value = cache._items['a']
        cache._items['a'] = (created_at - 5, value)

        self.assertIsNone(cache.get('a'))
        self.assertEqual(cache.stats()['items'], 0)

    def test_invalidate_older_than_removes_old_items(self) -> None:
        cache = SessionCache(ttl_seconds=100)
        cache.set('old', {'ok': True})
        cache.set('new', {'ok': True})
        _created_at, value = cache._items['old']
        cache._items['old'] = (time.time() - 500, value)

        removed = cache.invalidate_older_than(60)

        self.assertEqual(removed, 1)
        self.assertNotIn('old', cache._items)
        self.assertIn('new', cache._items)

    def test_invalidate_content_hash_checks_payload_and_snapshot(self) -> None:
        cache = SessionCache()
        cache.set('payload', {'content_hash': 'abc'})
        cache.set('snapshot', {'snapshot': {'content_hash': 'abc'}})
        cache.set('other', {'content_hash': 'def'})

        removed = cache.invalidate_content_hash('abc')

        self.assertEqual(removed, 2)
        self.assertEqual(set(cache._items), {'other'})

    def test_clear_returns_removed_count(self) -> None:
        cache = SessionCache()
        cache.set('a', 1)
        cache.set('b', 2)

        self.assertEqual(cache.clear(), 2)
        self.assertEqual(cache.stats()['items'], 0)


class CacheToolTests(unittest.TestCase):
    def test_invalidate_research_cache_tool(self) -> None:
        from mcp_server.server import cache, invalidate_research_cache

        cache.clear()
        cache.set('a', {'content_hash': 'abc'})
        cache.set('b', {'content_hash': 'def'})

        result = invalidate_research_cache(content_hash='abc')

        self.assertTrue(result['ok'])
        self.assertEqual(result['removed'], 1)
        self.assertEqual(result['cache']['items'], 1)
        cache.clear()


if __name__ == '__main__':
    unittest.main()
