from __future__ import annotations

import unittest
from pathlib import Path

from mcp_server.debug_tools import list_declared_tool_names
from web_research.config import Settings


class ConfigServerTests(unittest.TestCase):
    def test_validate_accepts_defaults(self) -> None:
        Settings(log_path=Path('data/test.log')).validate()

    def test_validate_rejects_bad_transport(self) -> None:
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), mcp_transport='websocket').validate()

    def test_server_exposes_only_assistant_style_tools(self) -> None:
        tools = list_declared_tool_names()

        self.assertEqual(tools, ['web_search', 'read_url', 'discover_links', 'research_web'])


if __name__ == '__main__':
    unittest.main()
