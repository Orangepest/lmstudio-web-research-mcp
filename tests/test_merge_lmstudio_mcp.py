from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from scripts.merge_lmstudio_mcp import load_existing_config, merge_config
from scripts.validate_lmstudio_mcp import validate_config


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

    def test_merge_config_can_generate_windows_paths(self) -> None:
        research_dir = Path('C:/Users/example/mcp-servers/lmstudio-web-research-mcp')
        merged = merge_config({}, research_dir=research_dir, platform='windows')
        web = merged['mcpServers']['web-research']

        self.assertEqual(
            web['command'],
            'C:/Users/example/mcp-servers/lmstudio-web-research-mcp/.venv/Scripts/python.exe',
        )
        self.assertEqual(web['cwd'], 'C:/Users/example/mcp-servers/lmstudio-web-research-mcp')
        self.assertEqual(web['env']['BROWSER_TIMEZONE_ID'], 'UTC')
        self.assertEqual(web['env']['SEARCH_PROVIDERS'], 'searxng_local_html,searxng_local,brave_html,duckduckgo_lite')
        self.assertEqual(validate_config(merged, research_dir=research_dir, platform='windows', check_paths=False), [])

    def test_load_existing_config_backs_up_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'mcp.json'
            config.write_text('', encoding='utf-8')
            empty = load_existing_config(config, backup_invalid=True)
            config.write_text('not json', encoding='utf-8')
            invalid = load_existing_config(config, backup_invalid=True)
            backups = list(Path(tmp).glob('mcp.json.invalid*'))

        self.assertEqual(empty, {})
        self.assertEqual(invalid, {})
        self.assertEqual(len(backups), 1)


if __name__ == '__main__':
    unittest.main()
