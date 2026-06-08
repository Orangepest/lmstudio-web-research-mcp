from __future__ import annotations

import unittest

from scripts.probe_mcp_server import DEFAULT_EXPECTED_TOOLS, tool_summary


class ProbeMCPServerTests(unittest.TestCase):
    def test_tool_summary_reports_expected_tool_set(self) -> None:
        result = tool_summary(list(DEFAULT_EXPECTED_TOOLS), target='stdio')

        self.assertTrue(result['ok'])
        self.assertEqual(result['tool_count'], len(DEFAULT_EXPECTED_TOOLS))
        self.assertEqual(result['missing_tools'], [])
        self.assertEqual(result['unexpected_tools'], [])

    def test_tool_summary_reports_missing_and_unexpected_tools(self) -> None:
        result = tool_summary(['web_search', 'extra_tool'], target='stdio')

        self.assertFalse(result['ok'])
        self.assertIn('safe_web_search', result['missing_tools'])
        self.assertEqual(result['unexpected_tools'], ['extra_tool', 'web_search'])


if __name__ == '__main__':
    unittest.main()
