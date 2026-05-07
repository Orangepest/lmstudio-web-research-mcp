from __future__ import annotations

import unittest

from web_research.recovery import build_recovery_candidates


class RecoveryTests(unittest.TestCase):
    def test_build_recovery_candidates_uses_same_domain_alternates(self) -> None:
        candidates = build_recovery_candidates('https://example.com/news/article?id=1')
        urls = [candidate.url for candidate in candidates]
        strategies = [candidate.strategy for candidate in candidates]

        self.assertIn('https://example.com/news/article?id=1&output=1', urls)
        self.assertIn('https://example.com/news/article/print', urls)
        self.assertIn('https://example.com/news/article.pdf', urls)
        self.assertIn('https://example.com/sitemap.xml', urls)
        self.assertIn('print_query', strategies)
        self.assertIn('sitemap', strategies)

    def test_build_recovery_candidates_rejects_non_http_urls(self) -> None:
        self.assertEqual(build_recovery_candidates('file:///tmp/article'), [])


if __name__ == '__main__':
    unittest.main()
