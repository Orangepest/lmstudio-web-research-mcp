from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web_research.runs import (
    build_research_context,
    find_research_runs,
    interrupt_research_checkpoint,
    list_research_checkpoints,
    list_research_runs,
    load_research_run,
    run_budget_summary,
    save_research_run,
    update_research_run,
)


class ResearchRunsTests(unittest.TestCase):
    def test_save_load_and_list_research_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                'ok': True,
                'sources': [{'source_id': 1}],
                'evidence': [{'citation': 'source:1[0:10]'}],
                'claims': [{'claim_id': 1}],
                'failures': [{'url': 'https://blocked.example', 'blocked': True}],
                'blocked_sources': [{'url': 'https://blocked.example', 'blocked': True}],
                'final_report': '# Report\n',
                'message': 'done',
            }

            saved = save_research_run('research_web', 'LM Studio MCP setup', payload, parent_run_id='parent-run', root=root)
            loaded = load_research_run(saved['run_id'], root=root)
            listed = list_research_runs(root=root)
            report_exists = Path(loaded['payload']['final_report_path']).exists()
            saved_report_exists = Path(saved['final_report_path']).exists()

        self.assertTrue(saved['saved'])
        self.assertTrue(saved_report_exists)
        self.assertTrue(loaded['ok'])
        self.assertEqual(loaded['run']['kind'], 'research_web')
        self.assertEqual(loaded['run']['parent_run_id'], 'parent-run')
        self.assertEqual(loaded['payload']['run_id'], saved['run_id'])
        self.assertTrue(report_exists)
        self.assertEqual(listed['count'], 1)
        self.assertEqual(listed['runs'][0]['source_count'], 1)
        self.assertEqual(listed['runs'][0]['evidence_count'], 1)
        self.assertEqual(listed['runs'][0]['claim_count'], 1)
        self.assertEqual(loaded['payload']['budget']['source_count'], 1)
        self.assertEqual(loaded['payload']['budget']['blocked_source_count'], 1)
        self.assertEqual(listed['runs'][0]['budget']['failure_count'], 1)
        self.assertEqual(listed['runs'][0]['title'], 'LM Studio MCP setup')
        self.assertTrue(listed['runs'][0]['short_answer'])
        self.assertEqual(listed['runs'][0]['parent_run_id'], 'parent-run')
        self.assertTrue(listed['runs'][0]['has_final_report'])
        self.assertEqual(listed['runs'][0]['suggested_actions'][0]['tool'], 'safe_continue_research_run')
        suggested_tools = [action['tool'] for action in listed['runs'][0]['suggested_actions']]
        self.assertIn('safe_export_research_run', suggested_tools)
        self.assertIn('safe_build_source_pack', suggested_tools)

    def test_saved_report_file_matches_selected_final_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                'ok': True,
                'report_format': 'source_table',
                'final_report': '# Sources\n\n| ID | Title | URL |\n',
            }

            saved = save_research_run('research_web', 'source table', payload, root=root)
            report_text = Path(saved['final_report_path']).read_text(encoding='utf-8')

        self.assertEqual(report_text, payload['final_report'])

    def test_load_rejects_unsafe_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = load_research_run('../nope', root=Path(tmp))

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['message'], 'Invalid research run id')

    def test_load_reports_corrupted_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('research_web', 'bad json', {'ok': True}, root=root)
            Path(saved['run_path']).write_text('{', encoding='utf-8')
            payload = load_research_run(saved['run_id'], root=root)

        self.assertFalse(payload['ok'])
        self.assertIn('Could not load research run', payload['message'])

    def test_load_reports_non_object_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('research_web', 'bad shape', {'ok': True}, root=root)
            Path(saved['run_path']).write_text('[]', encoding='utf-8')
            payload = load_research_run(saved['run_id'], root=root)

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['message'], 'Research run file is not a JSON object')

    def test_list_research_runs_applies_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_research_run('research_web', 'first query', {'ok': True}, root=root)
            save_research_run('deep_research', 'second query', {'ok': True}, root=root)
            listed = list_research_runs(limit=1, root=root)

        self.assertEqual(listed['count'], 1)
        self.assertEqual(listed['total_count'], 2)

    def test_list_research_runs_falls_back_to_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('research_web', 'fallback query', {'ok': True, 'sources': []}, root=root)
            Path(saved['run_path']).with_name('summary.json').unlink()
            listed = list_research_runs(root=root)

        self.assertEqual(listed['count'], 1)
        self.assertEqual(listed['runs'][0]['run_id'], saved['run_id'])

    def test_update_research_run_rewrites_payload_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('deep_research', 'checkpoint', {'ok': False}, root=root)
            updated = update_research_run(
                saved['run_id'],
                {'ok': True, 'final_report': '# Done\n', 'sources': [], 'evidence': [], 'claims': []},
                status='completed',
                root=root,
            )
            loaded = load_research_run(saved['run_id'], root=root)
            listed = list_research_runs(root=root)

        self.assertTrue(updated['ok'])
        self.assertEqual(loaded['run']['status'], 'completed')
        self.assertTrue(loaded['payload']['ok'])
        self.assertEqual(loaded['payload']['budget']['source_count'], 0)
        self.assertTrue(listed['runs'][0]['has_final_report'])

    def test_run_budget_summary_counts_work_units(self) -> None:
        budget = run_budget_summary(
            {
                'sources': [
                    {'source_id': 1, 'rendered': True, 'browser_interactions': {'scroll_steps': 2}},
                    {'source_id': 2, 'recovered_from': {'url': 'https://example.com/a'}},
                ],
                'evidence': [{'citation': 'source:1[0:10]'}],
                'failures': [{'blocked': True}, {'blocked': False}],
                'blocked_sources': [{'blocked': True}],
                'searches': [{'intent': 'initial'}, {'intent': 'gap_follow_up'}],
                'selection_trace': [{'decision': 'selected'}, {'decision': 'read_failed'}],
                'strategy': {'follow_up_rounds': 2, 'auto_follow_up_plan': [{'query': 'gap'}]},
                'agent_loop': {'rounds': [{'round': 1}, {'round': 2}]},
                'source_selection_telemetry': {
                    'planned_authority_source_count': 3,
                    'selected_authority_source_count': 1,
                    'planned_low_value_source_count': 2,
                    'planned_policy_skip_count': 1,
                    'repeated_domain_count': 1,
                },
            }
        )

        self.assertEqual(budget['source_count'], 2)
        self.assertEqual(budget['rendered_source_count'], 1)
        self.assertEqual(budget['browser_interacted_source_count'], 1)
        self.assertEqual(budget['recovered_source_count'], 1)
        self.assertEqual(budget['follow_up_search_count'], 1)
        self.assertEqual(budget['planned_follow_up_count'], 1)
        self.assertEqual(budget['follow_up_rounds_requested'], 2)
        self.assertEqual(budget['agent_round_count'], 2)
        self.assertEqual(budget['planned_authority_source_count'], 3)
        self.assertEqual(budget['selected_authority_source_count'], 1)
        self.assertEqual(budget['planned_low_value_source_count'], 2)
        self.assertEqual(budget['planned_policy_skip_count'], 1)
        self.assertEqual(budget['repeated_domain_count'], 1)

    def test_save_research_run_accepts_initial_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('deep_research', 'checkpoint', {'ok': False}, status='in_progress', root=root)
            loaded = load_research_run(saved['run_id'], root=root)
            listed = list_research_runs(root=root)

        self.assertEqual(loaded['run']['status'], 'in_progress')
        self.assertEqual(listed['runs'][0]['suggested_actions'][0]['tool'], 'safe_resume_deep_research')
        self.assertIn(saved['run_id'], listed['runs'][0]['suggested_actions'][0]['example'])

    def test_list_and_interrupt_research_checkpoints_preserves_resume_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run(
                'deep_research',
                'checkpoint',
                {
                    'ok': False,
                    'question': 'checkpoint',
                    'strategy': {'query_plan': [{'query': 'checkpoint source', 'intent': 'initial'}]},
                    'checkpoint': {'completed_queries': [], 'remaining_queries': ['checkpoint source']},
                },
                status='in_progress',
                root=root,
            )

            listed = list_research_checkpoints(root=root)
            interrupted = interrupt_research_checkpoint(saved['run_id'], root=root, message='stale')
            loaded = load_research_run(saved['run_id'], root=root)
            listed_interrupted = list_research_checkpoints(status='interrupted', root=root)

        self.assertTrue(listed['ok'])
        self.assertEqual(listed['count'], 1)
        self.assertTrue(interrupted['ok'])
        self.assertEqual(loaded['run']['status'], 'interrupted')
        self.assertEqual(loaded['payload']['checkpoint']['remaining_queries'], ['checkpoint source'])
        self.assertTrue(loaded['payload']['interruption']['resume_supported'])
        self.assertEqual(listed_interrupted['count'], 1)
        self.assertEqual(listed_interrupted['checkpoints'][0]['suggested_actions'][0]['tool'], 'safe_resume_deep_research')

    def test_interrupt_research_checkpoint_rejects_completed_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('deep_research', 'done', {'ok': True}, status='completed', root=root)
            result = interrupt_research_checkpoint(saved['run_id'], root=root)

        self.assertFalse(result['ok'])
        self.assertIn('Completed', result['message'])

    def test_find_research_runs_returns_relevant_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = save_research_run(
                'deep_research',
                'local AI coding assistant options',
                {
                    'ok': True,
                    'question': 'Compare local AI coding assistant options',
                    'claims': [{'claim': 'Local coding assistants can prioritize privacy.'}],
                    'sources': [{'source_id': 1}],
                    'research_quality': {'label': 'moderate', 'score': 60},
                },
                root=root,
            )
            save_research_run(
                'deep_research',
                'subscription cancellation rules',
                {
                    'ok': True,
                    'question': 'US subscription cancellation rules',
                    'claims': [{'claim': 'FTC guidance affects subscriptions.'}],
                    'sources': [{'source_id': 1}],
                },
                root=root,
            )

            result = find_research_runs('follow up on local coding assistant privacy', root=root)

        self.assertTrue(result['ok'])
        self.assertGreaterEqual(result['count'], 1)
        self.assertEqual(result['runs'][0]['run_id'], local['run_id'])
        self.assertIn('coding', result['runs'][0]['matched_terms'])

    def test_find_research_runs_prefers_newer_runs_when_scores_tie(self) -> None:
        def rewrite_created_at(saved: dict, created_at: str) -> None:
            run_path = Path(saved['run_path'])
            summary_path = run_path.with_name('summary.json')
            run = json.loads(run_path.read_text(encoding='utf-8'))
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
            run['run']['created_at'] = created_at
            run['run']['updated_at'] = created_at
            summary['created_at'] = created_at
            summary['updated_at'] = created_at
            run_path.write_text(json.dumps(run), encoding='utf-8')
            summary_path.write_text(json.dumps(summary), encoding='utf-8')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            newer = save_research_run('deep_research', 'target privacy run newer', {'ok': True}, root=root)
            older = save_research_run('deep_research', 'target privacy run older', {'ok': True}, root=root)
            rewrite_created_at(newer, '2026-01-07T00:00:00Z')
            rewrite_created_at(older, '2026-01-06T00:00:00Z')
            for day in range(8, 13):
                filler = save_research_run('deep_research', f'unrelated filler {day}', {'ok': True}, root=root)
                rewrite_created_at(filler, f'2026-01-{day:02d}T00:00:00Z')

            result = find_research_runs('privacy', limit=20, root=root)
            ordered_target_ids = [
                run['run_id']
                for run in result['runs']
                if run['run_id'] in {older['run_id'], newer['run_id']}
            ]

        self.assertEqual(ordered_target_ids, [newer['run_id'], older['run_id']])

    def test_build_research_context_returns_prior_context_and_next_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run(
                'deep_research',
                'local LLM web research assistant',
                {
                    'ok': True,
                    'question': 'Build a local LLM web research assistant',
                    'final_report': '# Executive Brief\n\nUse MCP tools for search, reading, and saved-run continuation.',
                    'sources': [
                        {
                            'source_id': 1,
                            'title': 'MCP documentation',
                            'url': 'https://example.com/mcp',
                            'domain': 'example.com',
                        }
                    ],
                    'evidence': [{'source_id': 1, 'citation': 'source:1[0:20]', 'text': 'Saved runs preserve prior context.'}],
                    'claims': [{'claim_id': 'C1', 'claim': 'Saved research runs can be continued.'}],
                    'recommended_next_searches': ['MCP research continuation examples'],
                    'research_quality': {'label': 'moderate', 'score': 65},
                },
                root=root,
            )

            result = build_research_context('continue local LLM research assistant work', root=root)

        self.assertTrue(result['ok'])
        self.assertTrue(result['matched'])
        self.assertEqual(result['selected_run_id'], saved['run_id'])
        self.assertEqual(result['next_tool']['tool'], 'safe_continue_research_run')
        self.assertIn(saved['run_id'], result['next_tool']['request'])
        self.assertIn('continue local LLM research assistant work', result['next_tool']['request'])
        self.assertIn('Next safe tool: safe_continue_research_run', result['context_prompt'])
        self.assertEqual(result['top_sources'][0]['url'], 'https://example.com/mcp')
        self.assertEqual(result['top_claims'][0]['claim'], 'Saved research runs can be continued.')

    def test_build_research_context_suggests_resume_for_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run(
                'deep_research',
                'unfinished local research',
                {'ok': False, 'question': 'unfinished local research'},
                status='in_progress',
                root=root,
            )

            result = build_research_context('continue unfinished local research', root=root)

        self.assertTrue(result['matched'])
        self.assertEqual(result['selected_run_id'], saved['run_id'])
        self.assertEqual(result['next_tool']['tool'], 'safe_resume_deep_research')
        self.assertEqual(result['next_tool']['request'], saved['run_id'])


class ResearchRunToolTests(unittest.TestCase):
    def test_mcp_run_tools_delegate_to_store(self) -> None:
        from mcp_server.server import find_research_runs as find_runs_tool
        from mcp_server.server import get_research_run, list_research_runs as list_runs_tool
        from mcp_server.server import safe_find_research_runs, safe_get_research_run, safe_list_research_runs, safe_research_context

        with patch('mcp_server.server.run_list_research_runs', return_value={'ok': True, 'runs': []}) as list_mock:
            self.assertEqual(list_runs_tool(limit=5), {'ok': True, 'runs': []})
            self.assertEqual(safe_list_research_runs(request='recent'), {'ok': True, 'runs': []})
        with patch('mcp_server.server.run_find_research_runs', return_value={'ok': True, 'runs': []}) as find_mock:
            self.assertEqual(find_runs_tool(query='local ai', limit=3), {'ok': True, 'runs': []})
            self.assertEqual(safe_find_research_runs(query='local ai'), {'ok': True, 'runs': []})
        with patch('mcp_server.server.build_research_context', return_value={'ok': True, 'tool': 'safe_research_context'}) as context_mock:
            self.assertEqual(safe_research_context(query='local ai'), {'ok': True, 'tool': 'safe_research_context'})
        with patch('mcp_server.server.load_research_run', return_value={'ok': True, 'run': {'run_id': 'abc', 'status': 'completed'}}) as load_mock:
            self.assertEqual(get_research_run('abc'), {'ok': True, 'run': {'run_id': 'abc', 'status': 'completed'}})
            safe_result = safe_get_research_run('abc')
            self.assertEqual(safe_result['ok'], True)
            self.assertEqual(safe_result['run'], {'run_id': 'abc', 'status': 'completed'})
            self.assertEqual(safe_result['payload'], {})
            self.assertEqual(safe_result['suggested_actions'][0]['tool'], 'safe_continue_research_run')

        self.assertEqual(list_mock.call_args_list[0].kwargs, {'limit': 5})
        self.assertEqual(list_mock.call_args_list[1].kwargs, {'limit': 10})
        self.assertEqual(find_mock.call_args_list[0].kwargs, {'query': 'local ai', 'limit': 3})
        self.assertEqual(find_mock.call_args_list[1].kwargs, {'query': 'local ai', 'limit': 5})
        self.assertEqual(context_mock.call_args.kwargs['query'], 'local ai')
        self.assertEqual(context_mock.call_args.kwargs['limit'], 3)
        self.assertEqual(load_mock.call_count, 2)


if __name__ == '__main__':
    unittest.main()
