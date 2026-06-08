from __future__ import annotations

import asyncio
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_server.debug_tools import extract_tool_info, list_declared_tool_names
from mcp_server.server import _registered_tool_names, repair_lmstudio_tool_call, safe_research_agent
from web_research.config import Settings


class ConfigServerTests(unittest.TestCase):
    def test_validate_accepts_defaults(self) -> None:
        Settings(log_path=Path('data/test.log')).validate()

    def test_validate_rejects_bad_transport(self) -> None:
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), mcp_transport='websocket').validate()

    def test_validate_rejects_bad_tool_profile(self) -> None:
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), mcp_tool_profile='everything').validate()

    def test_validate_accepts_agent_strict_tool_profile(self) -> None:
        Settings(log_path=Path('data/test.log'), mcp_tool_profile='agent_strict').validate()

    def test_validate_rejects_bad_cache_settings(self) -> None:
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), cache_ttl_seconds=0).validate()
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), cache_max_items=0).validate()

    def test_validate_rejects_bad_fetch_throttle_settings(self) -> None:
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), fetch_domain_delay_seconds=-1).validate()
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), fetch_block_backoff_seconds=-1).validate()
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), deep_research_soft_timeout_seconds=-1).validate()

    def test_validate_rejects_bad_browser_interaction_settings(self) -> None:
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), browser_scroll_steps=-1).validate()

    def test_validate_rejects_bad_local_llm_report_settings(self) -> None:
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), local_llm_report_max_tokens=0).validate()

    def test_validate_rejects_bad_compact_result_settings(self) -> None:
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), mcp_result_excerpt_chars=0).validate()
        with self.assertRaises(ValueError):
            Settings(log_path=Path('data/test.log'), mcp_result_max_items=0).validate()

    def test_server_exposes_only_assistant_style_tools(self) -> None:
        tools = list_declared_tool_names()

        self.assertEqual(
            tools,
            [
                'safe_web_search',
                'safe_repair_tool_call',
                'safe_research_agent',
                'safe_read_url',
                'safe_research',
                'safe_deep_research',
                'safe_research_mission',
                'safe_research_runtime',
                'safe_research_campaign',
                'safe_research_director',
                'safe_synthesize_research_campaign',
                'safe_resume_deep_research',
                'web_search',
                'read_url',
                'discover_links',
                'research_web',
                'deep_research',
                'list_research_runs',
                'safe_work_loop_status',
                'safe_cleanup_work_loops',
                'safe_submit_research_job',
                'safe_research_job_status',
                'safe_cancel_research_job',
                'safe_research_checkpoint_status',
                'safe_interrupt_research_checkpoints',
                'safe_list_research_runs',
                'safe_find_research_runs',
                'safe_research_context',
                'find_research_runs',
                'safe_get_research_run',
                'safe_export_research_run',
                'safe_build_source_pack',
                'get_research_run',
                'invalidate_research_cache',
                'resume_deep_research',
                'continue_research_run',
                'safe_continue_research_run',
            ],
        )

    def test_server_registers_only_safe_tools_by_default(self) -> None:
        tools = _registered_tool_names()

        self.assertEqual(
            tools,
            [
                'safe_web_search',
                'safe_repair_tool_call',
                'safe_research_agent',
                'safe_read_url',
                'safe_research',
                'safe_deep_research',
                'safe_research_mission',
                'safe_research_runtime',
                'safe_research_campaign',
                'safe_research_director',
                'safe_synthesize_research_campaign',
                'safe_resume_deep_research',
                'safe_work_loop_status',
                'safe_cleanup_work_loops',
                'safe_submit_research_job',
                'safe_research_job_status',
                'safe_cancel_research_job',
                'safe_research_checkpoint_status',
                'safe_interrupt_research_checkpoints',
                'safe_list_research_runs',
                'safe_find_research_runs',
                'safe_research_context',
                'safe_get_research_run',
                'safe_export_research_run',
                'safe_build_source_pack',
                'safe_continue_research_run',
            ],
        )
        self.assertNotIn('web_search', tools)

    def test_safe_tools_have_exactly_one_parameter(self) -> None:
        tools = extract_tool_info()
        safe_tools = {name: info for name, info in tools.items() if name.startswith('safe_')}

        self.assertEqual(
            sorted(safe_tools),
            [
                'safe_build_source_pack',
                'safe_cancel_research_job',
                'safe_cleanup_work_loops',
                'safe_continue_research_run',
                'safe_deep_research',
                'safe_export_research_run',
                'safe_find_research_runs',
                'safe_get_research_run',
                'safe_interrupt_research_checkpoints',
                'safe_list_research_runs',
                'safe_read_url',
                'safe_repair_tool_call',
                'safe_research',
                'safe_research_agent',
                'safe_research_campaign',
                'safe_research_checkpoint_status',
                'safe_research_context',
                'safe_research_director',
                'safe_research_job_status',
                'safe_research_mission',
                'safe_research_runtime',
                'safe_resume_deep_research',
                'safe_submit_research_job',
                'safe_synthesize_research_campaign',
                'safe_web_search',
                'safe_work_loop_status',
            ],
        )
        for name, info in safe_tools.items():
            self.assertEqual(len(info.parameters), 1, f'{name} should have exactly one parameter')
            self.assertTrue(info.docstring, f'{name} should explain its safe wrapper behavior')

    def test_repair_tool_call_rewrites_malformed_web_search_to_safe_search(self) -> None:
        result = repair_lmstudio_tool_call(
            '<tool_call> <function=web_search> <parameter> <parameter=max_results> 8 </parameter> '
            '<parameter=query> sex chat room model agency tactics "keep men chatting" retention strategy </parameter> '
            '</function> </tool_call>'
        )

        self.assertTrue(result['ok'])
        self.assertEqual(result['recommended_tool'], 'safe_web_search')
        self.assertEqual(result['recommended_parameter'], 'query')
        self.assertIn('<function=safe_web_search>', result['repaired_tool_call'])
        self.assertIn('sex chat room model agency tactics', result['repaired_tool_call'])
        self.assertNotIn('max_results', result['repaired_tool_call'])

    def test_safe_research_agent_routes_search_requests(self) -> None:
        with patch('mcp_server.server.safe_web_search', return_value={'ok': True, 'results': []}) as search:
            result = asyncio.run(safe_research_agent('search: current LM Studio MCP docs'))

        self.assertTrue(result['ok'])
        self.assertEqual(result['routed_by'], 'safe_research_agent')
        self.assertEqual(result['routed_action'], 'search')
        search.assert_called_once_with('current LM Studio MCP docs')

    def test_safe_research_agent_reads_detected_url_directly(self) -> None:
        with patch('mcp_server.server.safe_read_url', return_value={'ok': True, 'url': 'https://example.com/doc'}) as read_url:
            result = asyncio.run(safe_research_agent('Read https://example.com/doc and extract the key points.'))

        self.assertTrue(result['ok'])
        self.assertEqual(result['routed_by'], 'safe_research_agent')
        self.assertEqual(result['routed_action'], 'read')
        self.assertEqual(result['detected_urls'], ['https://example.com/doc'])
        read_url.assert_awaited_once_with('https://example.com/doc')

    def test_safe_research_agent_routes_heavy_requests_to_runtime(self) -> None:
        with patch('mcp_server.server.safe_research_runtime', return_value={'ok': True, 'tool': 'safe_research_runtime'}) as runtime:
            result = asyncio.run(safe_research_agent('deep research report on local MCP research tools'))

        self.assertTrue(result['ok'])
        self.assertEqual(result['routed_by'], 'safe_research_agent')
        self.assertEqual(result['routed_action'], 'runtime')
        runtime.assert_called_once()
        runtime_request = runtime.call_args.args[0]
        self.assertIn('submit=true', runtime_request)
        self.assertIn('start_worker=true', runtime_request)
        self.assertIn('apply=true', runtime_request)

    def test_safe_research_agent_allows_explicit_inline_deep(self) -> None:
        with patch('mcp_server.server.safe_deep_research', return_value={'ok': True, 'tool': 'safe_deep_research'}) as deep:
            result = asyncio.run(safe_research_agent('mode: inline_deep\nquestion: compare local MCP research tools'))

        self.assertTrue(result['ok'])
        self.assertEqual(result['routed_action'], 'deep')
        deep.assert_awaited_once_with('compare local MCP research tools')

    def test_readme_mcp_tools_section_matches_server_tools(self) -> None:
        readme = Path('README.md').read_text(encoding='utf-8')
        section = readme.split('## MCP Tools', 1)[1].split('## Quick Start', 1)[0]
        documented = re.findall(r'^- `([a-zA-Z0-9_]+)\(', section, flags=re.MULTILINE)

        self.assertEqual(documented, list_declared_tool_names())


if __name__ == '__main__':
    unittest.main()
