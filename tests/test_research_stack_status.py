from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.research_stack_status import (
    build_status,
    config_status,
    docs_status,
    format_status,
    lmstudio_runtime_status,
    prompt_status,
    refresh_prompt_file,
    runs_status,
    search_provider_status,
    tools_status,
)


class ResearchStackStatusTests(unittest.TestCase):
    def test_prompt_status_detects_matching_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / 'guide.md'
            output = root / 'prompt.txt'
            doc.write_text('## System Prompt\n\n```text\nUse safe_research and safe_deep_research.\n```\n', encoding='utf-8')
            output.write_text('Use safe_research and safe_deep_research.\n', encoding='utf-8')

            status = prompt_status(doc_path=doc, output_path=output)

        self.assertTrue(status['ok'])
        self.assertTrue(status['output_matches_doc'])
        self.assertTrue(status['mentions_safe_tools'])

    def test_prompt_status_rejects_stale_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / 'guide.md'
            output = root / 'prompt.txt'
            doc.write_text('## System Prompt\n\n```text\nUse safe_research.\n```\n', encoding='utf-8')
            output.write_text('Old prompt.\n', encoding='utf-8')

            status = prompt_status(doc_path=doc, output_path=output)

        self.assertFalse(status['ok'])
        self.assertFalse(status['output_matches_doc'])

    def test_refresh_prompt_file_rewrites_stale_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / 'guide.md'
            output = root / 'prompt.txt'
            doc.write_text('## System Prompt\n\n```text\nUse safe_research.\n```\n', encoding='utf-8')
            output.write_text('Old prompt.\n', encoding='utf-8')

            result = refresh_prompt_file(doc_path=doc, output_path=output)
            status = prompt_status(doc_path=doc, output_path=output)
            output_text = output.read_text(encoding='utf-8')

        self.assertTrue(result['ok'])
        self.assertTrue(status['ok'])
        self.assertEqual(output_text, 'Use safe_research.\n')

    def test_config_status_reports_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'mcp.json'
            config.write_text(json.dumps({'mcpServers': {}}), encoding='utf-8')

            status = config_status(config_path=config, research_dir=Path('/tmp/research'))

        self.assertFalse(status['ok'])
        self.assertIn('missing chrome-mcp-server entry', status['errors'])

    def test_search_provider_status_reports_config_and_recent_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'mcp.json'
            log = root / 'lmstudio.log'
            config.write_text(
                json.dumps(
                    {
                        'mcpServers': {
                            'web-research': {
                                'env': {
                                    'SEARCH_PROVIDERS': 'searxng_local_html,searxng_local,brave_html',
                                    'SEARCH_TIMEOUT': '4',
                                    'SEARCH_PROVIDER_BACKOFF_SECONDS': '600',
                                    'SEARCH_SIMILAR_CACHE': 'true',
                                    'SEARXNG_URL': 'http://127.0.0.1:8888',
                                    'SEARXNG_ENGINES': 'google',
                                }
                            }
                        }
                    }
                ),
                encoding='utf-8',
            )
            log.write_text(
                '[ERROR] HTTP Request: GET http://127.0.0.1:8888/search?q=x&format=json "HTTP/1.1" 403 Forbidden\n'
                '[ERROR] HTTP Request: GET https://search.brave.com/search?q=x "HTTP/1.1" 429 Too Many Requests\n',
                encoding='utf-8',
            )

            with patch('scripts.research_stack_status._latest_lmstudio_log_path', return_value=log):
                status = search_provider_status(config_path=config)

        self.assertTrue(status['ok'])
        self.assertEqual(status['configured']['provider_order'], ['searxng_local_html', 'searxng_local', 'brave_html'])
        self.assertEqual(status['configured']['searxng_engines'], 'google')
        self.assertEqual(status['recent_failures']['by_provider'], {'searxng_local': 1, 'brave_html': 1})
        self.assertIn('Recent LM Studio logs contain search-provider HTTP failures.', status['warnings'])

    def test_search_provider_status_can_run_live_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'mcp.json'
            config.write_text(json.dumps({'mcpServers': {'web-research': {'env': {}}}}), encoding='utf-8')

            with patch('scripts.research_stack_status._latest_lmstudio_log_path', return_value=None), patch(
                'scripts.research_stack_status.web_search',
                return_value={
                    'ok': True,
                    'provider': 'searxng_local_html',
                    'provider_order': ['searxng_local_html'],
                    'backend_attempts': [{'provider': 'searxng_local_html', 'ok': True, 'result_count': 3}],
                    'results': [{'url': 'https://example.com'}],
                },
            ) as probe:
                status = search_provider_status(config_path=config, probe=True, probe_query='health')

        self.assertTrue(status['ok'])
        self.assertEqual(status['live_probe']['provider'], 'searxng_local_html')
        self.assertEqual(status['live_probe']['result_count'], 1)
        probe.assert_called_once_with('health', max_results=3)

    def test_lmstudio_runtime_status_reports_context_and_bridge_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'lmstudio.log'
            log.write_text(
                "[2026-06-06 18:53:55][DEBUG] TruncateMiddle policy activated, pre-processing the '41872' token prompt by removing x\n"
                '[2026-06-06 18:54:00][ERROR] Processing request of type CallToolRequest\n'
                '[2026-06-06 18:54:03][ERROR] WebSocket connection closed\n'
                '[2026-06-06 18:54:04][ERROR] Failed to parse tool call: Expected "<parameter=", but got "<parameter>"\n'
                '[2026-06-06 18:54:05][ERROR] Starting MCP server transport=stdio host=127.0.0.1 port=8000 tools=1 tool_profile=agent_strict advanced_tools=False\n',
                encoding='utf-8',
            )

            status = lmstudio_runtime_status(log)

        self.assertFalse(status['ok'])
        self.assertEqual(status['truncation_count'], 1)
        self.assertEqual(status['max_prompt_tokens'], 41872)
        self.assertEqual(status['last_log_at'], '2026-06-06 18:54:05')
        self.assertEqual(status['last_truncation_at'], '2026-06-06 18:53:55')
        self.assertEqual(status['websocket_closes'], 1)
        self.assertEqual(status['last_websocket_close_at'], '2026-06-06 18:54:03')
        self.assertEqual(status['last_tool_call_at'], '2026-06-06 18:54:00')
        self.assertEqual(status['latest_plugin_start']['tool_profile'], 'agent_strict')
        self.assertIn('LM Studio is truncating the chat context; start a fresh chat or lower retained context for fewer malformed tool calls.', status['warnings'])

    def test_docs_status_checks_readme_and_prompt_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / 'README.md'
            doc = root / 'prompt.md'
            tools = [
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
            ]
            readme.write_text(
                '## MCP Tools\n\n'
                + ''.join(f'- `{tool}(x)`\n' for tool in tools)
                + '\n## Quick Start\n',
                encoding='utf-8',
            )
            doc.write_text(
                '## System Prompt\n\n```text\n'
                + ' '.join(tool for tool in tools if tool.startswith('safe_'))
                + '\n```\n',
                encoding='utf-8',
            )

            status = docs_status(readme_path=readme, doc_path=doc)

        self.assertTrue(status['ok'])
        self.assertEqual(status['prompt_missing_safe_tools'], [])

    def test_docs_status_reports_missing_safe_prompt_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / 'README.md'
            doc = root / 'prompt.md'
            readme.write_text('## MCP Tools\n\n- `safe_web_search(query)`\n\n## Quick Start\n', encoding='utf-8')
            doc.write_text('## System Prompt\n\n```text\nUse tools.\n```\n', encoding='utf-8')

            status = docs_status(readme_path=readme, doc_path=doc)

        self.assertFalse(status['ok'])
        self.assertIn('safe_research', status['prompt_missing_safe_tools'])

    def test_docs_status_uses_declared_server_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / 'README.md'
            doc = root / 'prompt.md'
            readme.write_text('## MCP Tools\n\n- `safe_web_search(query)`\n\n## Quick Start\n', encoding='utf-8')
            doc.write_text('## System Prompt\n\n```text\nsafe_web_search\n```\n', encoding='utf-8')

            with patch(
                'scripts.research_stack_status.list_declared_tool_names',
                return_value=['safe_web_search', 'safe_new_tool'],
            ):
                status = docs_status(readme_path=readme, doc_path=doc)

        self.assertFalse(status['ok'])
        self.assertEqual(status['server_tools'], ['safe_web_search', 'safe_new_tool'])
        self.assertIn('safe_new_tool', status['readme_missing_tools'])
        self.assertIn('safe_new_tool', status['prompt_missing_safe_tools'])

    def test_runs_status_counts_runs_and_archive_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / 'run-1'
            run_dir.mkdir()
            (run_dir / 'summary.json').write_text(
                json.dumps(
                    {
                        'run_id': 'run-1',
                        'kind': 'deep_research',
                        'created_at': '2026-01-01T00:00:00Z',
                        'updated_at': '2026-01-01T00:00:00Z',
                        'status': 'in_progress',
                    }
                ),
                encoding='utf-8',
            )
            (run_dir / 'run.json').write_text(
                json.dumps(
                    {
                        'run': {'run_id': 'run-1', 'kind': 'deep_research', 'status': 'in_progress'},
                        'payload': {
                            'sources': [{'source_id': 1, 'rendered': True}],
                            'blocked_sources': [{'blocked': True}],
                            'failures': [
                                {
                                    'url': 'https://www.researchgate.net/publication/123',
                                    'message': 'skipped by source policy: hostile_or_low_value_research_domain',
                                    'skipped': True,
                                    'skip_reason': 'hostile_or_low_value_research_domain',
                                },
                                {
                                    'url': 'https://example.com/blocked',
                                    'blocked': True,
                                    'recovery_skipped': True,
                                    'recovery_skip_reason': 'hard_block_or_no_recovery_domain',
                                },
                            ],
                            'selection_trace': [
                                {
                                    'url': 'https://www.researchgate.net/publication/123',
                                    'decision': 'skipped_source_policy',
                                    'skip_reason': 'hostile_or_low_value_research_domain',
                                }
                            ],
                        },
                    }
                ),
                encoding='utf-8',
            )

            status = runs_status(root=root, keep_latest=0, older_than_days=0)

        self.assertEqual(status['total_runs'], 1)
        self.assertEqual(status['status_counts'], {'in_progress': 1})
        self.assertEqual(status['archive_candidates'], 1)
        self.assertEqual(status['resumable'][0]['run_id'], 'run-1')
        self.assertEqual(status['latest'][0]['source_policy_audit']['skipped_source_count'], 1)
        self.assertEqual(status['latest_source_policy_audit']['hard_block_recovery_skip_count'], 1)
        self.assertEqual(
            status['latest_source_policy_audit']['skip_reason_counts'],
            {'hostile_or_low_value_research_domain': 2},
        )
        self.assertIn('safe_resume_deep_research', status['resumable'][0]['resume_tool_call'])
        self.assertEqual(status['latest'][0]['kind'], 'deep_research')
        self.assertEqual(status['latest'][0]['suggested_actions'][0]['tool'], 'safe_resume_deep_research')
        self.assertEqual(status['latest'][0]['budget']['source_count'], 1)
        self.assertEqual(status['latest_budget_totals']['rendered_source_count'], 1)
        self.assertEqual(status['latest_budget_totals']['blocked_source_count'], 1)

    def test_build_status_can_skip_tool_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'missing.json'
            with patch('scripts.research_stack_status.prompt_status', return_value={'ok': True}), patch(
                'scripts.research_stack_status.runs_status', return_value={'ok': True}
            ):
                status = build_status(
                    config_path=config,
                    research_dir=root,
                    runs_root=root / 'runs',
                    probe_tools=False,
                )

        self.assertFalse(status['ok'])
        self.assertTrue(status['tools']['probe_skipped'])

    def test_tools_status_probes_with_lmstudio_config_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'mcp.json'
            config.write_text(
                json.dumps({'mcpServers': {'web-research': {'env': {'MCP_TOOL_PROFILE': 'agent_strict'}}}}),
                encoding='utf-8',
            )

            with patch(
                'scripts.research_stack_status.run_stdio_probe',
                return_value={'tools': {'ok': True, 'tool_profile': 'agent_strict', 'tool_count': 1}},
            ) as probe:
                status = tools_status(research_dir=root, probe=True, config_path=config)

        self.assertTrue(status['ok'])
        self.assertEqual(status['tool_profile'], 'agent_strict')
        probe.assert_called_once()
        self.assertEqual(probe.call_args.kwargs['env_overrides']['MCP_TOOL_PROFILE'], 'agent_strict')

    def test_build_status_fails_when_prompt_refresh_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'mcp.json'
            config.write_text(
                json.dumps(
                    {
                        'mcpServers': {
                            'chrome-mcp-server': {'command': 'npx', 'args': ['chrome']},
                            'web-research': {'command': str(root / '.venv' / 'bin' / 'python'), 'args': ['-m', 'mcp_server.server']},
                        }
                    }
                ),
                encoding='utf-8',
            )
            with patch('scripts.research_stack_status.refresh_prompt_file', return_value={'ok': False}), patch(
                'scripts.research_stack_status.prompt_status', return_value={'ok': True}
            ), patch('scripts.research_stack_status.docs_status', return_value={'ok': True}), patch(
                'scripts.research_stack_status.runs_status', return_value={'ok': True}
            ):
                status = build_status(
                    config_path=config,
                    research_dir=root,
                    runs_root=root / 'runs',
                    probe_tools=False,
                    refresh_prompt=True,
                )

        self.assertFalse(status['ok'])
        self.assertEqual(status['refresh_prompt'], {'ok': False})

    def test_format_status_includes_major_sections(self) -> None:
        text = format_status(
            {
                'ok': True,
                'refresh_prompt': {
                    'ok': True,
                    'message': 'Prompt file refreshed from docs.',
                    'output_path': '/prompt.txt',
                },
                'prompt': {
                    'ok': True,
                    'doc_path': '/doc.md',
                    'output_path': '/prompt.txt',
                    'output_matches_doc': True,
                },
                'docs': {
                    'ok': True,
                    'readme_order_matches': True,
                    'prompt_missing_safe_tools': [],
                },
                'config': {
                    'ok': True,
                    'path': '/mcp.json',
                    'compact_results': 'true',
                    'result_excerpt_chars': '3500',
                    'result_max_items': '4',
                    'max_content_chars': '40000',
                    'browser_max_content_chars': '20000',
                    'browser_interaction': 'true',
                    'browser_scroll_steps': '4',
                    'search_providers': 'searxng_local_html,searxng_local,brave_html,duckduckgo_lite',
                    'search_timeout': '4',
                    'searxng_engines': 'google',
                    'local_synthesis': 'false',
                    'contradiction_review': 'false',
                    'errors': [],
                },
                'search': {
                    'ok': True,
                    'configured': {
                        'provider_order': ['searxng_local_html', 'searxng_local'],
                        'search_timeout': '4',
                        'searxng_url': 'http://127.0.0.1:8888',
                        'searxng_engines': 'google',
                    },
                    'recent_failures': {'by_provider': {'searxng_local': 2}},
                    'warnings': ['Recent LM Studio logs contain search-provider HTTP failures.'],
                    'live_probe': None,
                },
                'lmstudio_runtime': {
                    'ok': False,
                    'log_path': '/lmstudio.log',
                    'last_log_at': '2026-06-06 19:00:00',
                    'truncation_count': 3,
                    'max_prompt_tokens': 41000,
                    'last_truncation_at': '2026-06-06 18:59:00',
                    'websocket_closes': 1,
                    'last_websocket_close_at': '2026-06-06 18:59:30',
                    'last_tool_call_at': '2026-06-06 18:58:00',
                    'latest_plugin_start': {'tools': 1, 'tool_profile': 'agent_strict', 'advanced_tools': 'False'},
                    'warnings': ['LM Studio is truncating the chat context.'],
                },
                'runs': {
                    'total_runs': 1,
                    'archive_candidates': 0,
                    'status_counts': {'in_progress': 1},
                    'latest_budget_totals': {'source_count': 2, 'rendered_source_count': 1},
                    'resumable': [
                        {
                            'run_id': 'run-1',
                            'updated_at': '2026-01-01T00:00:00Z',
                            'resume_tool_call': 'safe_resume_deep_research(run_id="run-1")',
                        }
                    ],
                    'latest': [
                        {
                            'run_id': 'done-1',
                            'status': 'completed',
                            'suggested_actions': [
                                {
                                    'tool': 'safe_continue_research_run',
                                    'reason': 'Run can continue.',
                                    'example': 'safe_continue_research_run(request="done-1\\n<follow-up query>")',
                                },
                                {
                                    'tool': 'safe_export_research_run',
                                    'reason': 'Run can be exported.',
                                    'example': 'safe_export_research_run(request="done-1\\nprofile=private-share")',
                                },
                                {
                                    'tool': 'safe_build_source_pack',
                                    'reason': 'Run can be packed.',
                                    'example': 'safe_build_source_pack(request="done-1")',
                                }
                            ],
                        }
                    ],
                },
                'tools': {'probe_skipped': True, 'expected_tool_count': 15},
                'dry_run': {'enabled': True, 'message': 'No MCP server process was launched.'},
            }
        )

        self.assertIn('Research stack status: OK', text)
        self.assertIn('Prompt refresh: OK', text)
        self.assertIn('Docs alignment: OK', text)
        self.assertIn('LM Studio config', text)
        self.assertIn('Dry run: enabled', text)
        self.assertIn('result excerpt chars: 3500', text)
        self.assertIn('result max items: 4', text)
        self.assertIn('browser interaction: true', text)
        self.assertIn('Search providers: OK', text)
        self.assertIn('LM Studio runtime: CHECK', text)
        self.assertIn('context truncations: 3 max_prompt_tokens=41000', text)
        self.assertIn('last websocket close: 2026-06-06 18:59:30', text)
        self.assertIn("recent failures: {'searxng_local': 2}", text)
        self.assertIn("latest budget totals: {'source_count': 2, 'rendered_source_count': 1}", text)
        self.assertIn('browser scroll steps: 4', text)
        self.assertIn('MCP tools', text)
        self.assertIn('safe_resume_deep_research(run_id="run-1")', text)
        self.assertIn(
            'done-1 status=completed: safe_continue_research_run, safe_export_research_run, safe_build_source_pack',
            text,
        )


if __name__ == '__main__':
    unittest.main()
