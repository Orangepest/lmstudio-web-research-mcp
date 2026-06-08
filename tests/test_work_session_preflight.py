from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.work_session_preflight import (
    build_preflight,
    make_preflight_dir,
    preflight_markdown,
    risk_assessment,
    run_eval_smoke,
    summarize_eval_smoke,
)


class WorkSessionPreflightTests(unittest.TestCase):
    def test_make_preflight_dir_creates_unique_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = make_preflight_dir(root)
            second = make_preflight_dir(root)

        self.assertNotEqual(first, second)
        self.assertTrue(first.name)
        self.assertTrue(second.name)

    def test_risk_assessment_flags_work_session_risks(self) -> None:
        risk = risk_assessment(
            {
                'ok': True,
                'config': {'compact_results': 'false', 'browser_interaction': 'false'},
                'runs': {
                    'resumable': [{'run_id': 'run-1'}],
                    'latest_budget_totals': {'blocked_source_count': 3},
                },
                'tools': {'ok': True},
            }
        )

        codes = [item['code'] for item in risk['risks']]
        self.assertTrue(risk['ok'])
        self.assertIn('compact_results_disabled', codes)
        self.assertIn('browser_interaction_disabled', codes)
        self.assertIn('recent_blocked_sources', codes)
        self.assertIn('resumable_runs_pending', codes)

    def test_summarize_eval_smoke_counts_caps_and_source_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / 'summary.json').write_text(
                """{
                  "task_count": 2,
                  "average_score": 79.0,
                  "labels": {"borderline": 2},
                  "records": [
                    {
                      "score": {
                        "score_caps": [
                          {"cap": 79, "reason": "final_answer_review_failed"},
                          {"cap": 79, "reason": "conflicted_claims_not_resolution_searched"}
                        ],
                        "required_check_failures": ["contradiction_table_rows_present"],
                        "metrics": {
                          "buried_strong_selected_count": 2,
                          "selected_low_value_source_count": 0,
                          "planned_low_value_source_count": 1,
                          "contradiction_resolution_search_count": 1
                        }
                      }
                    },
                    {
                      "score": {
                        "score_caps": [{"cap": 79, "reason": "final_answer_review_failed"}],
                        "required_check_failures": [],
                        "metrics": {
                          "buried_strong_selected_count": 1,
                          "selected_low_value_source_count": 1,
                          "planned_low_value_source_count": 0,
                          "contradiction_resolution_search_count": 0
                        }
                      }
                    }
                  ]
                }""",
                encoding='utf-8',
            )

            summary = summarize_eval_smoke(output)

        self.assertTrue(summary['ok'])
        self.assertEqual(summary['task_count'], 2)
        self.assertEqual(summary['score_cap_count'], 3)
        self.assertEqual(summary['required_check_failure_count'], 1)
        self.assertEqual(summary['buried_strong_selected_count'], 3)
        self.assertEqual(summary['selected_low_value_source_count'], 1)
        self.assertEqual(summary['planned_low_value_source_count'], 1)
        self.assertEqual(summary['contradiction_resolution_search_count'], 1)
        self.assertEqual(summary['score_caps'][0], {'name': 'final_answer_review_failed', 'count': 2})

    def test_risk_assessment_flags_eval_summary_quality_risks(self) -> None:
        risk = risk_assessment(
            {
                'ok': True,
                'config': {'compact_results': 'true', 'browser_interaction': 'true'},
                'runs': {'resumable': [], 'latest_budget_totals': {}},
                'tools': {'ok': True},
            },
            eval_result={
                'returncode': 0,
                'summary': {
                    'score_cap_count': 2,
                    'selected_low_value_source_count': 1,
                },
            },
        )

        codes = [item['code'] for item in risk['risks']]
        self.assertTrue(risk['ok'])
        self.assertIn('eval_score_caps_present', codes)
        self.assertIn('eval_selected_low_value_sources', codes)

    def test_run_eval_smoke_uses_fixture_mode_by_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / 'tasks.json'
            fixture = root / 'fixture.json'
            (root / 'eval_smoke').mkdir()
            (root / 'eval_smoke' / 'summary.json').write_text('{"task_count": 0, "average_score": 0, "records": []}')
            with patch('scripts.work_session_preflight.subprocess.run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = 'ok'
                run.return_value.stderr = ''

                result = run_eval_smoke(
                    output_dir=root,
                    limit=3,
                    min_score=70,
                    min_average_score=75,
                    eval_mode='fixture',
                    tasks=tasks,
                    fixture=fixture,
                )

        command = result['command']
        self.assertEqual(result['mode'], 'fixture')
        self.assertEqual(result['tasks_path'], str(tasks))
        self.assertEqual(result['fixture_path'], str(fixture))
        self.assertIn('--fixture', command)
        self.assertIn(str(fixture), command)
        self.assertIn('--limit', command)
        self.assertIn('3', command)
        self.assertIn('--min-score', command)
        self.assertIn('70', command)
        self.assertIn('--min-average-score', command)
        self.assertIn('75', command)

    def test_run_eval_smoke_live_mode_omits_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / 'tasks.json'
            (root / 'eval_smoke').mkdir()
            (root / 'eval_smoke' / 'summary.json').write_text('{"task_count": 0, "average_score": 0, "records": []}')
            with patch('scripts.work_session_preflight.subprocess.run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ''
                run.return_value.stderr = ''

                result = run_eval_smoke(
                    output_dir=root,
                    limit=1,
                    min_score=None,
                    min_average_score=None,
                    eval_mode='live',
                    tasks=tasks,
                )

        self.assertEqual(result['mode'], 'live')
        self.assertEqual(result['tasks_path'], str(tasks))
        self.assertIsNone(result['fixture_path'])
        self.assertNotIn('--fixture', result['command'])

    def test_run_eval_smoke_rejects_invalid_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_eval_smoke(
                    output_dir=Path(tmp),
                    limit=1,
                    min_score=None,
                    min_average_score=None,
                    eval_mode='offline',
                )

    def test_risk_assessment_fails_high_risk_preflight(self) -> None:
        risk = risk_assessment({'ok': False, 'config': {}, 'runs': {}, 'tools': {'ok': False}})

        self.assertFalse(risk['ok'])
        self.assertGreaterEqual(risk['high_count'], 1)

    def test_preflight_markdown_includes_status_and_risks(self) -> None:
        text = preflight_markdown(
            {
                'ok': False,
                'completed_at': 'now',
                'output_dir': '/tmp/preflight',
                'risk': {
                    'risk_count': 1,
                    'risks': [{'severity': 'high', 'code': 'stack_status_failed', 'message': 'Stack failed.'}],
                },
                'status': {
                    'ok': False,
                    'dry_run': {'enabled': True, 'message': 'No MCP server process was launched.'},
                    'prompt': {'ok': True, 'doc_path': '/doc', 'output_path': '/prompt', 'output_matches_doc': True},
                    'docs': {'ok': True, 'readme_order_matches': True, 'prompt_missing_safe_tools': []},
                    'config': {
                        'ok': True,
                        'path': '/mcp.json',
                        'compact_results': 'true',
                        'max_content_chars': '40000',
                        'browser_max_content_chars': '20000',
                        'browser_interaction': 'true',
                        'browser_scroll_steps': '4',
                        'local_synthesis': 'false',
                        'contradiction_review': 'false',
                    },
                    'runs': {'total_runs': 0, 'archive_candidates': 0, 'status_counts': {}, 'latest_budget_totals': {}},
                    'tools': {'probe_skipped': True, 'expected_tool_count': 20},
                },
                'eval_smoke': {
                    'returncode': 0,
                    'mode': 'fixture',
                    'tasks_path': '/tmp/tasks.json',
                    'fixture_path': '/tmp/fixture.json',
                    'output_dir': '/tmp/preflight/eval_smoke',
                    'summary_path': '/tmp/preflight/eval_smoke/summary.md',
                    'summary': {
                        'task_count': 2,
                        'average_score': 79.0,
                        'labels': {'borderline': 2},
                        'score_cap_count': 1,
                        'score_caps': [{'name': 'final_answer_review_failed', 'count': 1}],
                        'required_check_failure_count': 1,
                        'failed_required_checks': [{'name': 'contradiction_table_rows_present', 'count': 1}],
                        'buried_strong_selected_count': 2,
                        'selected_low_value_source_count': 0,
                        'planned_low_value_source_count': 1,
                        'contradiction_resolution_search_count': 1,
                    },
                },
            }
        )

        self.assertIn('# Work Session Preflight', text)
        self.assertIn('stack_status_failed', text)
        self.assertIn('Dry run: enabled', text)
        self.assertIn('Mode: fixture', text)
        self.assertIn('Fixture: /tmp/fixture.json', text)
        self.assertIn('Average score: 79.0/100', text)
        self.assertIn('final_answer_review_failed', text)
        self.assertIn('2 buried strong selected / 0 low-value selected / 1 low-value planned', text)

    def test_build_preflight_uses_status_and_eval_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / 'tasks.json'
            fixture = root / 'fixture.json'
            with patch(
                'scripts.work_session_preflight.build_status',
                return_value={
                    'ok': True,
                    'config': {'compact_results': 'true', 'browser_interaction': 'true'},
                    'runs': {'resumable': [], 'latest_budget_totals': {}},
                    'tools': {'probe_skipped': True},
                },
            ), patch(
                'scripts.work_session_preflight.run_eval_smoke',
                return_value={'returncode': 0, 'summary_path': '/tmp/eval/summary.md'},
            ) as run_eval:
                preflight = build_preflight(
                    output_dir=root,
                    config_path=root / 'mcp.json',
                    research_dir=root,
                    runs_root=root / 'runs',
                    probe_tools=True,
                    dry_run=True,
                    eval_smoke=True,
                    eval_limit=1,
                    min_score=60,
                    min_average_score=60,
                    eval_mode='fixture',
                    eval_tasks=tasks,
                    eval_fixture=fixture,
                    profile='private-share',
                )

        self.assertTrue(preflight['ok'])
        self.assertTrue(preflight['dry_run'])
        self.assertFalse(preflight['probe_tools'])
        self.assertEqual(preflight['profile']['name'], 'private-share')
        self.assertTrue(preflight['profile']['redact_exports'])
        self.assertEqual(preflight['eval_smoke']['returncode'], 0)
        run_eval.assert_called_once_with(
            output_dir=root,
            limit=1,
            min_score=60,
            min_average_score=60,
            eval_mode='fixture',
            tasks=tasks,
            fixture=fixture,
        )


if __name__ == '__main__':
    unittest.main()
