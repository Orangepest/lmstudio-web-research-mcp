from __future__ import annotations

import unittest
from pathlib import Path

from scripts.validate_lmstudio_mcp import validate_config


class LMStudioMCPConfigTests(unittest.TestCase):
    def test_validate_config_accepts_expected_shape(self) -> None:
        research_dir = Path('/Users/example/mcp-servers/lmstudio-web-research-mcp')
        data = {
            'mcpServers': {
                'chrome-mcp-server': {'url': 'http://127.0.0.1:12306/mcp'},
                'web-research': {
                    'command': str(research_dir / '.venv/bin/python'),
                    'args': ['-m', 'mcp_server.server'],
                    'cwd': str(research_dir),
                    'env': {
                        'MCP_TRANSPORT': 'stdio',
                        'WEB_RESEARCH_LOG_PATH': '.runtime/web_research.log',
                        'RESEARCH_RUNS_DIR': '.runtime/research_runs',
                        'SEARXNG_URL': 'http://127.0.0.1:8888',
                        'SEARCH_PROVIDERS': 'searxng_local_html,searxng_local,brave_html,duckduckgo_lite',
                        'SEARCH_TIMEOUT': '4',
                        'SEARCH_PROVIDER_BACKOFF_SECONDS': '600',
                        'SEARCH_SIMILAR_CACHE': 'true',
                        'SEARXNG_ENGINES': 'google',
                        'SEARXNG_ENABLED_ENGINES': '',
                        'SEARXNG_DISABLED_ENGINES': '',
                        'ALLOWED_DOMAINS': '*',
                        'USER_AGENT': 'test-agent',
                        'REQUEST_TIMEOUT': '12',
                        'FETCH_BLOCK_BACKOFF_SECONDS': '3',
                        'DEEP_RESEARCH_SOFT_TIMEOUT_SECONDS': '35',
                        'MAX_CONTENT_CHARS': '40000',
                        'CACHE_TTL_SECONDS': '3600',
                        'CACHE_MAX_ITEMS': '128',
                        'BROWSER_HEADLESS': 'true',
                        'BROWSER_TIMEOUT_MS': '12000',
                        'BROWSER_MAX_CONTENT_CHARS': '20000',
                        'BROWSER_INTERACTION': 'true',
                        'BROWSER_SCROLL_STEPS': '4',
                        'BROWSER_LOCALE': 'en-US',
                        'BROWSER_TIMEZONE_ID': 'Asia/Seoul',
                        'BROWSER_PROFILE_DIR': '',
                        'MCP_COMPACT_RESULTS': 'true',
                        'MCP_TOOL_PROFILE': 'agent_strict',
                        'MCP_EXPOSE_ADVANCED_TOOLS': 'false',
                        'MCP_RESULT_EXCERPT_CHARS': '3500',
                        'MCP_RESULT_MAX_ITEMS': '4',
                        'LOCAL_LLM_CONTRADICTION_REVIEW': 'false',
                        'LOCAL_LLM_REPORT_SYNTHESIS': 'false',
                        'LOCAL_LLM_BASE_URL': 'http://127.0.0.1:1234/v1',
                        'LOCAL_LLM_MODEL': 'auto',
                        'LOCAL_LLM_TIMEOUT': '8',
                        'LOCAL_LLM_REPORT_MAX_TOKENS': '1800',
                    },
                },
            }
        }

        self.assertEqual(validate_config(data, research_dir=research_dir, check_paths=False), [])

    def test_validate_config_rejects_missing_web_research(self) -> None:
        errors = validate_config(
            {'mcpServers': {'chrome-mcp-server': {'url': 'http://127.0.0.1:12306/mcp'}}},
            research_dir=Path('/tmp/research'),
        )

        self.assertIn('missing web-research entry', errors)


if __name__ == '__main__':
    unittest.main()
