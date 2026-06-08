from __future__ import annotations

import unittest
from pathlib import Path

from scripts.merge_lmstudio_mcp import merge_config


class MergeLMStudioMCPTests(unittest.TestCase):
    def test_merge_config_preserves_unrelated_servers(self) -> None:
        research_dir = Path('/Users/example/mcp-servers/lmstudio-web-research-mcp')
        existing = {
            'mcpServers': {
                'custom-server': {
                    'command': '/bin/echo',
                    'args': ['hello'],
                },
                'web-research': {
                    'command': '/old/python',
                },
            }
        }

        merged = merge_config(existing, research_dir=research_dir)

        self.assertIn('custom-server', merged['mcpServers'])
        self.assertEqual(merged['mcpServers']['custom-server']['command'], '/bin/echo')
        self.assertEqual(
            merged['mcpServers']['web-research']['command'],
            '/Users/example/mcp-servers/lmstudio-web-research-mcp/.venv/bin/python',
        )
        self.assertEqual(
            merged['mcpServers']['web-research']['env']['RESEARCH_RUNS_DIR'],
            '.runtime/research_runs',
        )
        self.assertEqual(merged['mcpServers']['web-research']['env']['MCP_TOOL_PROFILE'], 'agent_strict')
        self.assertEqual(merged['mcpServers']['web-research']['env']['MCP_EXPOSE_ADVANCED_TOOLS'], 'false')
        self.assertEqual(merged['mcpServers']['web-research']['env']['MCP_RESULT_EXCERPT_CHARS'], '3500')
        self.assertEqual(merged['mcpServers']['web-research']['env']['MCP_RESULT_MAX_ITEMS'], '4')
        self.assertEqual(merged['mcpServers']['chrome-mcp-server']['url'], 'http://127.0.0.1:12306/mcp')


if __name__ == '__main__':
    unittest.main()
