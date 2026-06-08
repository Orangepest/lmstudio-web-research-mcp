from __future__ import annotations

import unittest
from datetime import UTC, datetime

from web_research.freshness import build_freshness_summary, is_current_sensitive


class FreshnessTests(unittest.TestCase):
    def test_is_current_sensitive_detects_latest_questions(self) -> None:
        self.assertTrue(is_current_sensitive('What is the latest LM Studio release?'))
        self.assertFalse(is_current_sensitive('Explain MCP architecture'))

    def test_build_freshness_summary_flags_current_sensitive_without_recent_evidence(self) -> None:
        summary = build_freshness_summary(
            {
                'question': 'latest MCP behavior',
                'sources': [{'source_id': 1, 'title': 'Old docs', 'text': 'Stable behavior from 2022.'}],
                'evidence': [{'quote': 'Stable behavior from 2022.'}],
                'recent_changes': [],
            },
            now=datetime(2026, 6, 5, tzinfo=UTC),
        )

        self.assertTrue(summary['current_sensitive'])
        self.assertFalse(summary['content_freshness_evidence'])
        self.assertEqual(summary['newest_mentioned_year'], 2022)
        self.assertTrue(summary['gaps'])

    def test_build_freshness_summary_accepts_recent_update_evidence(self) -> None:
        summary = build_freshness_summary(
            {
                'question': 'current MCP behavior',
                'sources': [{'source_id': 1, 'title': 'Changelog', 'text': 'Updated in 2026 with new MCP behavior.'}],
                'evidence': [{'quote': 'Updated in 2026 with new MCP behavior.'}],
                'recent_changes': [{'note': 'Updated in 2026.'}],
            },
            now=datetime(2026, 6, 5, tzinfo=UTC),
        )

        self.assertTrue(summary['content_freshness_evidence'])
        self.assertEqual(summary['newest_mentioned_year'], 2026)
        self.assertEqual(summary['recent_change_count'], 1)


if __name__ == '__main__':
    unittest.main()
