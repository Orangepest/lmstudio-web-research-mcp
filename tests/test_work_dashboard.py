from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.work_dashboard import (
    action_drilldown_markdown,
    apply_remediation_execution_tracking,
    build_action_history,
    build_action_drilldown,
    build_dashboard,
    build_dashboard_action_summary,
    build_dashboard_remediation_plan,
    collect_evals,
    collect_preflights,
    collect_remediation_benchmarks,
    collect_work_loops,
    dashboard_markdown,
    load_remediation_execution_events,
    load_action_snapshots,
    load_latest_action_snapshot,
    remediation_plan_markdown,
    write_action_drilldown_exports,
    write_action_snapshot,
    write_remediation_execution_event,
    write_remediation_plan,
)


class WorkDashboardTests(unittest.TestCase):
    def test_collect_preflights_summarizes_runtime_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / 'preflight-1'
            item.mkdir()
            (item / 'preflight.json').write_text(
                json.dumps(
                    {
                        'ok': True,
                        'completed_at': 'now',
                        'dry_run': True,
                        'probe_tools': False,
                        'risk': {'risk_count': 1, 'high_count': 0, 'medium_count': 1},
                        'eval_smoke': {
                            'returncode': 0,
                            'mode': 'fixture',
                            'tasks_path': '/tmp/tasks.json',
                            'fixture_path': '/tmp/fixture.json',
                            'summary_path': '/tmp/eval-summary.md',
                            'summary_json_path': '/tmp/eval-summary.json',
                            'summary': {
                                'task_count': 2,
                                'average_score': 79.0,
                                'labels': {'borderline': 2},
                                'score_cap_count': 3,
                                'score_caps': [
                                    {'name': 'final_answer_review_failed', 'count': 2},
                                    {'name': 'citation_audit_failed', 'count': 1},
                                ],
                                'required_check_failure_count': 1,
                                'failed_required_checks': [{'name': 'contradiction_table_rows_present', 'count': 1}],
                                'buried_strong_selected_count': 2,
                                'selected_low_value_source_count': 1,
                                'planned_low_value_source_count': 0,
                                'contradiction_resolution_search_count': 1,
                            },
                        },
                        'status': {
                            'runs': {
                                'total_runs': 2,
                                'latest_budget_totals': {'source_count': 5, 'blocked_source_count': 1},
                            }
                        },
                    }
                ),
                encoding='utf-8',
            )

            result = collect_preflights(root)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['id'], 'preflight-1')
        self.assertEqual(result[0]['risk_count'], 1)
        self.assertEqual(result[0]['latest_budget_totals']['source_count'], 5)
        self.assertTrue(result[0]['eval_smoke_enabled'])
        self.assertEqual(result[0]['eval_smoke_mode'], 'fixture')
        self.assertEqual(result[0]['eval_smoke_returncode'], 0)
        self.assertEqual(result[0]['eval_smoke_summary_path'], '/tmp/eval-summary.md')
        self.assertEqual(result[0]['eval_smoke_tasks_path'], '/tmp/tasks.json')
        self.assertEqual(result[0]['eval_smoke_fixture_path'], '/tmp/fixture.json')
        self.assertEqual(result[0]['eval_smoke_task_count'], 2)
        self.assertEqual(result[0]['eval_smoke_average_score'], 79.0)
        self.assertEqual(result[0]['eval_smoke_score_cap_count'], 3)
        self.assertEqual(result[0]['eval_smoke_required_check_failure_count'], 1)
        self.assertEqual(result[0]['eval_smoke_buried_strong_selected_count'], 2)
        self.assertEqual(result[0]['eval_smoke_selected_low_value_source_count'], 1)
        self.assertEqual(result[0]['eval_smoke_contradiction_resolution_search_count'], 1)

    def test_collect_preflights_handles_old_and_partial_eval_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_item = root / 'preflight-old'
            old_item.mkdir()
            (old_item / 'preflight.json').write_text(
                json.dumps({'ok': True, 'risk': {}, 'status': {'runs': {}}}),
                encoding='utf-8',
            )
            partial_item = root / 'preflight-partial'
            partial_item.mkdir()
            (partial_item / 'preflight.json').write_text(
                json.dumps(
                    {
                        'ok': True,
                        'eval_smoke': {
                            'returncode': 1,
                            'mode': 'live',
                            'summary_path': '/tmp/live-summary.md',
                        },
                        'risk': {},
                        'status': {'runs': {}},
                    }
                ),
                encoding='utf-8',
            )

            result = collect_preflights(root)

        by_id = {item['id']: item for item in result}
        self.assertFalse(by_id['preflight-old']['eval_smoke_enabled'])
        self.assertIsNone(by_id['preflight-old']['eval_smoke_mode'])
        self.assertTrue(by_id['preflight-partial']['eval_smoke_enabled'])
        self.assertEqual(by_id['preflight-partial']['eval_smoke_mode'], 'live')
        self.assertEqual(by_id['preflight-partial']['eval_smoke_returncode'], 1)
        self.assertEqual(by_id['preflight-partial']['eval_smoke_task_count'], 0)
        self.assertEqual(by_id['preflight-partial']['eval_smoke_score_cap_count'], 0)

    def test_collect_evals_summarizes_eval_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / 'eval-1'
            item.mkdir()
            (item / 'summary.json').write_text(
                json.dumps(
                    {
                        'ok': True,
                        'completed_at': 'now',
                        'task_count': 2,
                        'average_score': 82.5,
                        'labels': {'pass': 2},
                        'thresholds': {'ok': True, 'failure_count': 0},
                        'records': [
                            {
                                'task': {'id': 'task-a'},
                                'score': {
                                    'score': 82,
                                    'metrics': {
                                        'buried_strong_selected_count': 2,
                                        'selected_low_value_source_count': 0,
                                    },
                                    'weakest_checks': [],
                                },
                            }
                        ],
                    }
                ),
                encoding='utf-8',
            )
            older = root / 'eval-0'
            older.mkdir()
            (older / 'summary.json').write_text(
                json.dumps(
                    {
                        'ok': True,
                        'completed_at': 'earlier',
                        'task_count': 1,
                        'average_score': 70,
                        'labels': {'borderline': 1},
                        'records': [{'task': {'id': 'task-a'}, 'score': {'score': 70, 'metrics': {}, 'weakest_checks': []}}],
                    }
                ),
                encoding='utf-8',
            )

            result = collect_evals(root)

        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]['ok'])
        self.assertEqual(result[0]['average_score'], 82.5)
        self.assertEqual(result[0]['trend_average_score_delta'], 12.5)
        self.assertEqual(result[0]['trend_buried_strong_selected_delta'], 2)
        self.assertEqual(result[0]['trend_selected_low_value_source_delta'], 0)

    def test_collect_remediation_benchmarks_summarizes_ci_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / '20260606T090000Z-old'
            older.mkdir()
            (older / 'remediation_learning_benchmark.json').write_text(
                json.dumps(
                    {
                        'ok': True,
                        'scenario_count': 4,
                        'passed': 4,
                        'failed': 0,
                        'records': [{'id': 'missing-primary', 'ok': True, 'failure_count': 0}],
                    }
                ),
                encoding='utf-8',
            )
            latest = root / '20260606T091000Z-new'
            latest.mkdir()
            (latest / 'ci_check.md').write_text('# CI\n', encoding='utf-8')
            (latest / 'remediation_learning_benchmark.json').write_text(
                json.dumps(
                    {
                        'ok': False,
                        'scenario_count': 4,
                        'passed': 3,
                        'failed': 1,
                        'records': [
                            {'id': 'missing-primary', 'ok': False, 'failure_count': 2},
                            {'id': 'conflict-learning', 'ok': True, 'failure_count': 0},
                        ],
                    }
                ),
                encoding='utf-8',
            )

            result = collect_remediation_benchmarks(root)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['id'], '20260606T091000Z-new')
        self.assertFalse(result[0]['ok'])
        self.assertEqual(result[0]['scenario_count'], 4)
        self.assertEqual(result[0]['passed'], 3)
        self.assertEqual(result[0]['failed'], 1)
        self.assertEqual(result[0]['strategy_failure_count'], 2)
        self.assertEqual(result[0]['failed_scenarios'], ['missing-primary'])
        self.assertEqual(result[0]['trend_passed_delta'], -1)
        self.assertEqual(result[0]['trend_failed_delta'], 1)
        self.assertEqual(result[0]['report_path'], str(Path(tmp) / '20260606T091000Z-new' / 'ci_check.md'))

    def test_collect_work_loops_summarizes_loop_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / 'loop-1'
            item.mkdir()
            (item / 'work_loop.json').write_text(
                json.dumps(
                    {
                        'ok': False,
                        'in_progress': False,
                        'pid': os.getpid(),
                        'updated_at': 'now',
                        'completed_at': 'done',
                        'profile': {'name': 'careful'},
                        'cycle_count': 3,
                        'failed_cycle_count': 1,
                        'consecutive_failure_count': 1,
                        'stop_reason': 'consecutive_failures',
                    }
                ),
                encoding='utf-8',
            )

            result = collect_work_loops(root)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['id'], 'loop-1')
        self.assertFalse(result[0]['ok'])
        self.assertEqual(result[0]['profile'], 'careful')
        self.assertEqual(result[0]['cycle_count'], 3)
        self.assertEqual(result[0]['failed_cycle_count'], 1)
        self.assertEqual(result[0]['stop_reason'], 'consecutive_failures')
        self.assertIn('failed', result[0]['issue_codes'])
        self.assertFalse(result[0]['cleanup_eligible'])
        self.assertIsNone(result[0]['cleanup_apply_command'])
        self.assertTrue(result[0]['failed_unacknowledged'])
        self.assertIn('--review-failed --loop-id loop-1 --json', result[0]['review_preview_command'])

    def test_collect_work_loops_marks_missing_pid_in_progress_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / 'loop-stale'
            item.mkdir()
            (item / 'work_loop.json').write_text(
                json.dumps({'ok': False, 'in_progress': True, 'profile': {'name': 'careful'}, 'cycle_count': 1}),
                encoding='utf-8',
            )

            result = collect_work_loops(root)

        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]['in_progress'])
        self.assertTrue(result[0]['reported_in_progress'])
        self.assertTrue(result[0]['stale'])
        self.assertIsNone(result[0]['pid_alive'])
        self.assertIn('stale', result[0]['issue_codes'])
        self.assertIn('not_running', result[0]['issue_codes'])
        self.assertFalse(result[0]['cleanup_eligible'])
        self.assertIn('missing_pid_requires_include_legacy_missing_pid', result[0]['cleanup_blockers'])
        self.assertIn('--include-legacy-missing-pid', result[0]['cleanup_apply_command'])

    def test_collect_work_loops_reads_reviewed_failed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / 'loop-reviewed'
            item.mkdir()
            (item / 'work_loop.json').write_text(
                json.dumps(
                    {
                        'ok': False,
                        'in_progress': False,
                        'profile': {'name': 'careful'},
                        'cycle_count': 1,
                        'failed_cycle_count': 1,
                        'review': {'reviewed': True, 'reviewed_at': 'done', 'note': 'accepted old failure'},
                    }
                ),
                encoding='utf-8',
            )

            result = collect_work_loops(root)

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['reviewed'])
        self.assertEqual(result[0]['reviewed_at'], 'done')
        self.assertEqual(result[0]['review_note'], 'accepted old failure')
        self.assertIn('reviewed_failed', result[0]['issue_codes'])
        self.assertFalse(result[0]['failed_unacknowledged'])
        self.assertIsNone(result[0]['review_apply_command'])

    def test_reviewed_stale_loop_still_requires_stale_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / 'loop-reviewed-stale'
            item.mkdir()
            (item / 'work_loop.json').write_text(
                json.dumps(
                    {
                        'ok': False,
                        'in_progress': True,
                        'profile': {'name': 'careful'},
                        'review': {'reviewed': True, 'reviewed_at': 'done'},
                    }
                ),
                encoding='utf-8',
            )

            result = collect_work_loops(root)

        self.assertTrue(result[0]['reviewed'])
        self.assertTrue(result[0]['stale'])
        self.assertIn('stale', result[0]['issue_codes'])
        self.assertIn('reviewed_failed', result[0]['issue_codes'])
        self.assertIn('missing_pid_requires_include_legacy_missing_pid', result[0]['cleanup_blockers'])

    def test_collect_work_loops_marks_dead_pid_stale_loop_cleanup_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / 'loop-stale-dead-pid'
            item.mkdir()
            (item / 'work_loop.json').write_text(
                json.dumps({'ok': False, 'in_progress': True, 'pid': -1, 'profile': {'name': 'careful'}, 'cycle_count': 1}),
                encoding='utf-8',
            )

            result = collect_work_loops(root)

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['stale'])
        self.assertTrue(result[0]['cleanup_eligible'])
        self.assertEqual(result[0]['cleanup_blockers'], [])
        self.assertIn('python scripts/cleanup_work_loops.py --loop-id loop-stale-dead-pid --json', result[0]['cleanup_preview_command'])
        self.assertIn('python scripts/cleanup_work_loops.py --apply --loop-id loop-stale-dead-pid --json', result[0]['cleanup_apply_command'])

    def test_collect_work_loops_does_not_cleanup_active_live_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / 'loop-active'
            item.mkdir()
            (item / 'work_loop.json').write_text(
                json.dumps({'ok': True, 'in_progress': True, 'pid': os.getpid(), 'profile': {'name': 'careful'}, 'cycle_count': 1}),
                encoding='utf-8',
            )

            result = collect_work_loops(root)

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['in_progress'])
        self.assertFalse(result[0]['stale'])
        self.assertEqual(result[0]['issue_codes'], [])
        self.assertFalse(result[0]['cleanup_eligible'])
        self.assertIsNone(result[0]['cleanup_apply_command'])

    def test_dashboard_markdown_includes_preflights_and_evals(self) -> None:
        dashboard = build_dashboard(
            [
                {
                    'id': 'preflight-1',
                    'ok': True,
                    'risk_count': 0,
                    'high_count': 0,
                    'medium_count': 0,
                    'dry_run': True,
                    'probe_tools': False,
                    'eval_smoke_enabled': True,
                    'eval_smoke_mode': 'fixture',
                    'eval_smoke_returncode': 0,
                    'eval_smoke_summary_path': '/tmp/eval-smoke-summary.md',
                    'eval_smoke_task_count': 2,
                    'eval_smoke_average_score': 79.0,
                    'eval_smoke_score_cap_count': 3,
                    'eval_smoke_score_caps': [
                        {'name': 'final_answer_review_failed', 'count': 2},
                        {'name': 'citation_audit_failed', 'count': 1},
                    ],
                    'eval_smoke_buried_strong_selected_count': 2,
                    'eval_smoke_selected_low_value_source_count': 1,
                    'eval_smoke_planned_low_value_source_count': 0,
                    'latest_budget_totals': {'source_count': 3, 'blocked_source_count': 0},
                    'report_path': '/tmp/preflight.md',
                }
            ],
            [
                {
                    'id': 'eval-1',
                    'ok': True,
                    'task_count': 1,
                    'average_score': 90,
                    'labels': {'pass': 1},
                    'threshold_failure_count': 0,
                    'trend_average_score_delta': 4,
                    'trend_regression_count': 0,
                    'trend_improvement_count': 1,
                    'trend_buried_strong_selected_delta': 2,
                    'trend_selected_low_value_source_delta': -1,
                    'report_path': '/tmp/summary.md',
                }
            ],
            [
                {
                    'id': 'loop-1',
                    'ok': False,
                    'in_progress': False,
                    'stale': True,
                    'issue_codes': ['failed', 'not_running', 'stale'],
                    'cleanup_eligible': True,
                    'cleanup_preview_command': 'python scripts/cleanup_work_loops.py --loop-id loop-1 --json',
                    'cleanup_apply_command': 'python scripts/cleanup_work_loops.py --apply --loop-id loop-1 --json',
                    'cleanup_blockers': [],
                    'pid': -1,
                    'profile': 'careful',
                    'cycle_count': 2,
                    'failed_cycle_count': 1,
                    'consecutive_failure_count': 0,
                    'stop_reason': 'max_cycles',
                    'report_path': '/tmp/work_loop.md',
                    'events_path': '/tmp/events.jsonl',
                }
            ],
            remediation_benchmarks=[
                {
                    'id': 'ci-1',
                    'ok': False,
                    'scenario_count': 4,
                    'passed': 3,
                    'failed': 1,
                    'strategy_failure_count': 2,
                    'trend_passed_delta': -1,
                    'trend_failed_delta': 1,
                    'failed_scenarios': ['missing-primary'],
                    'report_path': '/tmp/ci_check.md',
                }
            ],
        )
        text = dashboard_markdown(dashboard)

        self.assertFalse(dashboard['ok'])
        self.assertIn('## Recent Work Loops', text)
        self.assertIn('loop-1', text)
        self.assertIn('failed, not_running, stale', text)
        self.assertIn('python scripts/cleanup_work_loops.py --loop-id loop-1 --json', text)
        self.assertIn('### Work Loop Guidance', text)
        self.assertIn('apply with `python scripts/cleanup_work_loops.py --apply --loop-id loop-1 --json`', text)
        self.assertIn('## Recent Preflights', text)
        self.assertIn('preflight-1', text)
        self.assertIn('fixture 2 task(s) / 79.0', text)
        self.assertIn('| preflight-1 | yes | 0 | 0 | 0 | yes | no | fixture 2 task(s) / 79.0 | yes |', text)
        self.assertIn('3 (final_answer_review_failed:2, citation_audit_failed:1)', text)
        self.assertIn('2 buried / 1 low / 0 planned low', text)
        self.assertIn('[/tmp/eval-smoke-summary.md](/tmp/eval-smoke-summary.md)', text)
        self.assertIn('## Recent Evals', text)
        self.assertIn('eval-1', text)
        self.assertIn('| eval-1 | yes | 1 | 90 | 4 | 0 | 1 | +2 buried / -1 low-value | pass:1 | 0 |', text)
        self.assertIn('## Remediation Learning Benchmarks', text)
        self.assertIn('| ci-1 | no | 4 | 3 | 1 | 2 | passed -1 / failed +1 | missing-primary | [/tmp/ci_check.md](/tmp/ci_check.md) |', text)

    def test_reviewed_failed_work_loop_does_not_fail_dashboard(self) -> None:
        dashboard = build_dashboard(
            [],
            [],
            [
                {
                    'id': 'loop-reviewed',
                    'ok': False,
                    'in_progress': False,
                    'reviewed': True,
                    'reviewed_at': 'done',
                    'review_note': 'accepted old failure',
                    'stale': False,
                    'issue_codes': ['reviewed_failed'],
                    'failed_unacknowledged': False,
                    'profile': 'careful',
                    'cycle_count': 1,
                    'failed_cycle_count': 1,
                    'consecutive_failure_count': 0,
                    'stop_reason': 'duration_elapsed',
                    'report_path': '/tmp/work_loop.md',
                    'events_path': '/tmp/events.jsonl',
                }
            ],
        )
        text = dashboard_markdown(dashboard)

        self.assertTrue(dashboard['ok'])
        self.assertEqual(dashboard['work_loop_failures'], 0)
        self.assertEqual(dashboard['reviewed_work_loop_failures'], 1)
        self.assertIn('reviewed failed loop', text)
        self.assertIn('accepted old failure', text)

    def test_reviewed_stale_work_loop_still_fails_dashboard(self) -> None:
        dashboard = build_dashboard(
            [],
            [],
            [
                {
                    'id': 'loop-reviewed-stale',
                    'ok': False,
                    'in_progress': False,
                    'reviewed': True,
                    'stale': True,
                    'issue_codes': ['reviewed_failed', 'stale'],
                    'failed_unacknowledged': False,
                    'profile': 'careful',
                    'cycle_count': 1,
                    'failed_cycle_count': 1,
                    'consecutive_failure_count': 0,
                    'stop_reason': 'duration_elapsed',
                    'report_path': '/tmp/work_loop.md',
                    'events_path': '/tmp/events.jsonl',
                }
            ],
        )

        self.assertFalse(dashboard['ok'])
        self.assertEqual(dashboard['work_loop_failures'], 0)
        self.assertEqual(dashboard['stale_work_loop_count'], 1)

    def test_build_dashboard_action_summary_covers_operational_risks(self) -> None:
        summary = build_dashboard_action_summary(
            [
                {
                    'id': 'preflight-high',
                    'ok': False,
                    'risk_count': 1,
                    'high_count': 1,
                    'medium_count': 0,
                    'report_path': '/tmp/preflight.md',
                    'eval_smoke_enabled': True,
                    'eval_smoke_returncode': 1,
                    'eval_smoke_score_cap_count': 2,
                    'eval_smoke_score_caps': [{'name': 'citation_audit_failed', 'count': 2}],
                    'eval_smoke_selected_low_value_source_count': 1,
                    'eval_smoke_planned_low_value_source_count': 1,
                    'eval_smoke_summary_path': '/tmp/eval-smoke.md',
                    'latest_budget_totals': {'blocked_source_count': 1},
                }
            ],
            [
                {
                    'id': 'eval-bad',
                    'ok': False,
                    'labels': {'fail': 1},
                    'threshold_failure_count': 1,
                    'trend_regression_count': 2,
                    'trend_average_score_delta': -10,
                    'trend_selected_low_value_source_delta': 1,
                    'trend_buried_strong_selected_delta': -1,
                    'report_path': '/tmp/eval.md',
                }
            ],
            [
                {
                    'id': 'loop-stale',
                    'ok': False,
                    'stale': True,
                    'cleanup_eligible': True,
                    'cleanup_preview_command': 'python scripts/cleanup_work_loops.py --loop-id loop-stale --json',
                    'cleanup_apply_command': 'python scripts/cleanup_work_loops.py --apply --loop-id loop-stale --json',
                    'cleanup_blockers': [],
                    'report_path': '/tmp/stale.md',
                },
                {
                    'id': 'loop-failed',
                    'ok': False,
                    'stale': False,
                    'failed_unacknowledged': True,
                    'review_preview_command': 'python scripts/cleanup_work_loops.py --review-failed --loop-id loop-failed --json',
                    'review_apply_command': 'python scripts/cleanup_work_loops.py --review-failed --apply --loop-id loop-failed --json',
                    'report_path': '/tmp/failed.md',
                },
                {
                    'id': 'loop-reviewed',
                    'ok': False,
                    'stale': False,
                    'reviewed': True,
                    'review_note': 'accepted',
                    'report_path': '/tmp/reviewed.md',
                },
            ],
            [
                {
                    'id': 'ci-bad',
                    'ok': False,
                    'failed': 1,
                    'strategy_failure_count': 2,
                    'trend_failed_delta': 1,
                    'report_path': '/tmp/ci.md',
                }
            ],
            {
                'ok': False,
                'event_count': 1,
                'failure_count': 1,
                'regression_count': 2,
                'events': [
                    {
                        'id': 'ci-timeline-bad',
                        'ok': False,
                        'stack_ok': False,
                        'stack_search_failure_count': 2,
                        'fixture_eval_ok': False,
                        'fixture_eval_threshold_failure_count': 1,
                        'fixture_eval_average_delta': -5,
                        'fixture_eval_summary_md_path': '/tmp/eval-fixture.md',
                        'remediation_ok': False,
                        'remediation_failed': 1,
                        'remediation_failed_delta': 1,
                        'remediation_failed_scenarios': ['missing-primary'],
                        'remediation_path': '/tmp/remediation.json',
                        'report_path': '/tmp/ci-timeline.md',
                        'risk_flags': [
                            'stack_failed',
                            'fixture_eval_failed',
                            'eval_threshold_failures',
                            'eval_score_drop',
                            'remediation_failed',
                            'remediation_scenarios_failed',
                            'remediation_regression',
                            'search_provider_failures',
                        ],
                    }
                ],
            },
        )

        categories = [action['category'] for action in summary['actions']]
        severities = [action['severity'] for action in summary['actions']]
        self.assertGreaterEqual(summary['action_count'], 10)
        self.assertEqual(severities[0], 'high')
        self.assertIn('work_loop', categories)
        self.assertIn('preflight', categories)
        self.assertIn('preflight_eval', categories)
        self.assertIn('eval', categories)
        self.assertIn('remediation_benchmark', categories)
        self.assertIn('quality_timeline', categories)
        self.assertIn('source_selection', categories)
        self.assertTrue(any(action['status'] == 'acknowledged' for action in summary['actions']))
        self.assertTrue(any(action['apply_command'] for action in summary['actions']))
        self.assertTrue(any(action['report_path'] == '/tmp/eval-smoke.md' for action in summary['actions']))
        self.assertTrue(any(action['report_path'] == '/tmp/eval-fixture.md' for action in summary['actions']))

    def test_dashboard_markdown_includes_quality_timeline(self) -> None:
        dashboard = build_dashboard(
            [],
            [],
            [],
            quality_timeline={
                'ok': False,
                'event_count': 1,
                'failure_count': 1,
                'regression_count': 1,
                'events': [
                    {
                        'id': 'ci-timeline-bad',
                        'ok': False,
                        'fixture_eval_ok': False,
                        'fixture_eval_threshold_failure_count': 1,
                        'fixture_eval_average_delta': -5,
                        'fixture_eval_summary_md_path': '/tmp/eval-fixture.md',
                        'risk_flags': ['fixture_eval_failed', 'eval_threshold_failures', 'eval_score_drop'],
                        'report_path': '/tmp/ci-timeline.md',
                    }
                ],
            },
        )
        text = dashboard_markdown(dashboard)

        self.assertFalse(dashboard['ok'])
        self.assertEqual(dashboard['quality_timeline_event_count'], 1)
        self.assertIn('Quality timeline: 1 events, 1 failures, 1 regressions', text)
        self.assertIn('## Quality Timeline', text)
        self.assertIn('ci-timeline-bad', text)
        self.assertIn('quality_timeline', text)
        self.assertIn('[/tmp/eval-fixture.md](/tmp/eval-fixture.md)', text)

    def test_quality_timeline_actions_group_repeated_issue_types(self) -> None:
        summary = build_dashboard_action_summary(
            [],
            [],
            [],
            quality_timeline={
                'ok': False,
                'event_count': 2,
                'failure_count': 2,
                'regression_count': 0,
                'events': [
                    {
                        'id': 'ci-new',
                        'ok': False,
                        'stack_ok': False,
                        'risk_flags': ['stack_failed'],
                        'report_path': '/tmp/ci-new.md',
                    },
                    {
                        'id': 'ci-old',
                        'ok': False,
                        'stack_ok': False,
                        'risk_flags': ['stack_failed'],
                        'report_path': '/tmp/ci-old.md',
                    },
                ],
            },
        )

        quality_actions = [action for action in summary['actions'] if action['category'] == 'quality_timeline']
        self.assertEqual(len(quality_actions), 1)
        self.assertEqual(quality_actions[0]['subject_id'], 'stack-check-failed')
        self.assertEqual(quality_actions[0]['status'], 'recurring')
        self.assertIn('Seen in 2 recent CI run(s): ci-new, ci-old', quality_actions[0]['summary'])
        self.assertEqual(quality_actions[0]['details']['occurrence_count'], 2)
        self.assertEqual(quality_actions[0]['details']['event_ids'], ['ci-new', 'ci-old'])

    def test_dashboard_acknowledges_recurring_quality_timeline_actions(self) -> None:
        previous_action = {
            'id': 'quality_timeline:stack-check-failed:ci-stack-check-failed',
            'severity': 'high',
            'category': 'quality_timeline',
            'summary': 'old stack failure',
        }
        dashboard = build_dashboard(
            [],
            [],
            [],
            quality_timeline={
                'ok': False,
                'event_count': 1,
                'failure_count': 1,
                'regression_count': 0,
                'events': [
                    {
                        'id': 'ci-new',
                        'ok': False,
                        'stack_ok': False,
                        'risk_flags': ['stack_failed'],
                        'report_path': '/tmp/ci-new.md',
                    }
                ],
            },
            previous_actions=[previous_action],
            previous_snapshot_path='/tmp/actions.json',
            prior_action_snapshots=[{'path': '/tmp/actions.json', 'actions': [previous_action]}],
        )

        quality_actions = [action for action in dashboard['visible_actions'] if action['category'] == 'quality_timeline']
        self.assertEqual(len(quality_actions), 1)
        self.assertEqual(quality_actions[0]['status'], 'acknowledged_recurring')
        self.assertEqual(quality_actions[0]['details']['seen_snapshot_count'], 2)
        self.assertEqual(dashboard['suppressed_action_count'], 0)

    def test_dashboard_markdown_includes_action_summary(self) -> None:
        dashboard = build_dashboard(
            [{'id': 'preflight-risk', 'ok': True, 'medium_count': 1, 'report_path': '/tmp/preflight.md'}],
            [],
            [],
        )
        text = dashboard_markdown(dashboard)

        self.assertIn('## Action Summary', text)
        self.assertIn('| Severity | Category | Item | Status | Action | Command | Report |', text)
        self.assertIn('preflight-risk', text)
        self.assertEqual(dashboard['actions'], dashboard['action_summary']['actions'])

    def test_dashboard_markdown_renders_empty_action_summary(self) -> None:
        dashboard = build_dashboard([], [], [])
        text = dashboard_markdown(dashboard)

        self.assertEqual(dashboard['action_count'], 0)
        self.assertIn('No dashboard actions currently flagged.', text)
        self.assertIn('Previous snapshot: none yet', text)

    def test_build_action_history_marks_new_recurring_and_resolved_actions(self) -> None:
        history = build_action_history(
            [
                {'id': 'eval:current', 'severity': 'high', 'category': 'eval', 'summary': 'current'},
                {'id': 'loop:recurring', 'severity': 'low', 'category': 'work_loop', 'summary': 'recurring'},
            ],
            [
                {'id': 'loop:recurring', 'severity': 'low', 'category': 'work_loop', 'summary': 'old recurring'},
                {'id': 'preflight:resolved', 'severity': 'medium', 'category': 'preflight', 'summary': 'resolved'},
            ],
            previous_snapshot_path='/tmp/previous.json',
            prior_snapshots=[
                {
                    'path': '/tmp/previous.json',
                    'actions': [
                        {'id': 'loop:recurring', 'severity': 'low', 'category': 'work_loop', 'summary': 'old recurring'},
                        {'id': 'preflight:resolved', 'severity': 'medium', 'category': 'preflight', 'summary': 'resolved'},
                    ],
                }
            ],
        )

        self.assertTrue(history['has_previous'])
        self.assertEqual(history['previous_snapshot_path'], '/tmp/previous.json')
        self.assertEqual(history['new_action_count'], 1)
        self.assertEqual(history['recurring_action_count'], 1)
        self.assertEqual(history['resolved_action_count'], 1)
        self.assertEqual(history['suppressed_action_count'], 1)
        self.assertEqual(history['age_by_action_id']['loop:recurring']['seen_snapshot_count'], 2)
        self.assertEqual(history['new_actions'][0]['id'], 'eval:current')
        self.assertEqual(history['recurring_actions'][0]['id'], 'loop:recurring')
        self.assertEqual(history['resolved_actions'][0]['id'], 'preflight:resolved')

    def test_dashboard_markdown_includes_action_history_counts(self) -> None:
        dashboard = build_dashboard(
            [{'id': 'preflight-risk', 'ok': True, 'medium_count': 1, 'report_path': '/tmp/preflight.md'}],
            [],
            [],
            previous_actions=[{'id': 'old:resolved', 'severity': 'low', 'category': 'eval'}],
            previous_snapshot_path='/tmp/actions.json',
        )
        text = dashboard_markdown(dashboard)

        self.assertIn('## Action History', text)
        self.assertIn('[/tmp/actions.json](/tmp/actions.json)', text)
        self.assertIn('New actions: 1', text)
        self.assertIn('Recurring actions: 0', text)
        self.assertIn('Resolved actions: 1', text)

    def test_dashboard_suppresses_recurring_low_risk_actions_from_primary_table(self) -> None:
        previous_action = {
            'id': 'preflight:blocked:blocked-sources-in-latest-run',
            'severity': 'low',
            'category': 'preflight',
            'summary': 'old blocked',
        }
        dashboard = build_dashboard(
            [
                {
                    'id': 'blocked',
                    'ok': True,
                    'medium_count': 0,
                    'latest_budget_totals': {'blocked_source_count': 2},
                    'report_path': '/tmp/preflight.md',
                }
            ],
            [],
            [],
            previous_actions=[previous_action],
            previous_snapshot_path='/tmp/actions.json',
            prior_action_snapshots=[{'path': '/tmp/actions.json', 'actions': [previous_action]}],
        )
        text = dashboard_markdown(dashboard)

        self.assertEqual(dashboard['action_count'], 1)
        self.assertEqual(dashboard['visible_action_count'], 0)
        self.assertEqual(dashboard['suppressed_action_count'], 1)
        self.assertIn('Suppressed recurring low-risk actions: 1', text)

    def test_action_drilldown_includes_paths_commands_and_recurrence(self) -> None:
        previous_action = {
            'id': 'work_loop:loop-stale:stale-work-loop-artifact',
            'severity': 'medium',
            'category': 'work_loop',
            'summary': 'old stale loop',
        }
        dashboard = build_dashboard(
            [],
            [],
            [
                {
                    'id': 'loop-stale',
                    'ok': False,
                    'stale': True,
                    'cleanup_eligible': True,
                    'cleanup_preview_command': 'python scripts/cleanup_work_loops.py --loop-id loop-stale --json',
                    'cleanup_apply_command': 'python scripts/cleanup_work_loops.py --apply --loop-id loop-stale --json',
                    'cleanup_blockers': [],
                    'report_path': '/tmp/stale.md',
                }
            ],
            previous_actions=[previous_action],
            previous_snapshot_path='/tmp/actions.json',
            prior_action_snapshots=[{'path': '/tmp/actions.json', 'actions': [previous_action]}],
        )

        drilldown = build_action_drilldown(dashboard['visible_actions'][0], dashboard)
        text = action_drilldown_markdown(drilldown)

        self.assertEqual(drilldown['id'], 'work_loop:loop-stale:stale-work-loop-artifact')
        self.assertEqual(drilldown['recurrence']['seen_snapshot_count'], 2)
        self.assertTrue(drilldown['recurrence']['is_recurring'])
        self.assertIn('/tmp/stale.md', drilldown['related_paths'])
        self.assertEqual(drilldown['next_commands'][0]['kind'], 'preview')
        self.assertIn('cleanup_work_loops.py --loop-id loop-stale', drilldown['next_commands'][0]['command'])
        self.assertIn('## Recurrence', text)
        self.assertIn('## Next Commands', text)
        self.assertIn('[/tmp/stale.md](/tmp/stale.md)', text)

    def test_write_action_drilldown_exports_writes_index_and_action_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard = build_dashboard(
                [{'id': 'preflight-risk', 'ok': True, 'medium_count': 1, 'report_path': '/tmp/preflight.md'}],
                [],
                [],
            )
            result = write_action_drilldown_exports(root, dashboard)

            index = json.loads((root / 'index.json').read_text(encoding='utf-8'))
            index_md = (root / 'index.md').read_text(encoding='utf-8')
            action_json_path = Path(result['actions'][0]['json_path'])
            action_md_path = Path(result['actions'][0]['markdown_path'])
            action_payload = json.loads(action_json_path.read_text(encoding='utf-8'))

        self.assertTrue(result['ok'])
        self.assertEqual(result['action_count'], 1)
        self.assertEqual(index['action_count'], 1)
        self.assertTrue(action_json_path.name.endswith('.json'))
        self.assertTrue(action_md_path.name.endswith('.md'))
        self.assertEqual(action_payload['category'], 'preflight')
        self.assertIn('/tmp/preflight.md', action_payload['related_paths'])
        self.assertIn('Work Dashboard Action Drilldowns', index_md)

    def test_dashboard_remediation_plan_prioritizes_and_dedupes_commands(self) -> None:
        dashboard = build_dashboard(
            [{'id': 'preflight-risk', 'ok': True, 'medium_count': 1, 'report_path': '/tmp/preflight.md'}],
            [
                {
                    'id': 'eval-bad',
                    'ok': False,
                    'labels': {'fail': 1},
                    'threshold_failure_count': 1,
                    'report_path': '/tmp/eval.md',
                }
            ],
            [
                {
                    'id': 'loop-stale',
                    'ok': False,
                    'stale': True,
                    'cleanup_eligible': True,
                    'cleanup_preview_command': 'python scripts/cleanup_work_loops.py --loop-id loop-stale --json',
                    'cleanup_apply_command': 'python scripts/cleanup_work_loops.py --apply --loop-id loop-stale --json',
                    'cleanup_blockers': [],
                    'report_path': '/tmp/stale.md',
                }
            ],
        )
        plan = build_dashboard_remediation_plan(dashboard)

        self.assertEqual(plan['step_count'], 3)
        self.assertEqual(plan['steps'][0]['kind'], 'eval')
        self.assertEqual(plan['steps'][0]['severity'], 'high')
        cleanup_step = next(step for step in plan['steps'] if step['kind'] == 'cleanup')
        self.assertEqual(cleanup_step['severity'], 'medium')
        self.assertIn('cleanup_work_loops.py --apply --loop-id loop-stale', cleanup_step['apply_command'])
        kinds = [step['kind'] for step in plan['steps']]
        self.assertIn('eval', kinds)
        self.assertIn('work_session', kinds)
        self.assertEqual(len({step['apply_command'] or step['preview_command'] for step in plan['steps']}), 3)

    def test_dashboard_remediation_plan_maps_quality_timeline_to_ci(self) -> None:
        dashboard = build_dashboard(
            [],
            [],
            [],
            quality_timeline={
                'ok': False,
                'event_count': 1,
                'failure_count': 1,
                'regression_count': 0,
                'events': [
                    {
                        'id': 'ci-bad',
                        'ok': False,
                        'stack_ok': False,
                        'risk_flags': ['stack_failed'],
                        'report_path': '/tmp/ci.md',
                    }
                ],
            },
        )

        plan = dashboard['remediation_plan']
        text = remediation_plan_markdown(plan)

        self.assertEqual(plan['step_count'], 1)
        self.assertEqual(plan['steps'][0]['kind'], 'ci')
        self.assertIn('research_ci_check.py --json', plan['steps'][0]['apply_command'])
        self.assertIn('## Notes', text)
        self.assertIn('Refresh fixture eval, remediation benchmark, stack probe', text)

    def test_write_remediation_plan_writes_markdown_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {
                'ok': False,
                'step_count': 1,
                'high_count': 1,
                'medium_count': 0,
                'steps': [
                    {
                        'rank': 1,
                        'severity': 'high',
                        'kind': 'ci',
                        'summary': 'CI failed',
                        'preview_command': 'python scripts/research_ci_check.py --skip-probe --limit 1 --json',
                        'apply_command': 'python scripts/research_ci_check.py --json',
                        'why': 'refresh CI',
                    }
                ],
            }
            paths = write_remediation_plan(root / 'plan.md', plan)
            written = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
            markdown = (root / 'plan.md').read_text(encoding='utf-8')

        self.assertEqual(paths['markdown_path'], str(root / 'plan.md'))
        self.assertEqual(paths['json_path'], str(root / 'plan.json'))
        self.assertEqual(written['step_count'], 1)
        self.assertIn('Work Dashboard Remediation Plan', markdown)
        self.assertIn('research_ci_check.py --json', markdown)

    def test_remediation_execution_tracking_marks_steps(self) -> None:
        plan = {
            'ok': False,
            'step_count': 1,
            'high_count': 1,
            'medium_count': 0,
            'steps': [
                {
                    'rank': 1,
                    'id': 'eval:bad:eval-threshold-failure',
                    'severity': 'high',
                    'kind': 'eval',
                    'summary': 'Eval failed',
                    'preview_command': 'python scripts/run_research_eval.py --profile careful --limit 1',
                    'apply_command': 'python scripts/run_research_eval.py --profile careful',
                }
            ],
        }
        tracked = apply_remediation_execution_tracking(
            plan,
            [
                {
                    'step_id': 'eval:bad:eval-threshold-failure',
                    'status': 'previewed',
                    'created_at': 'earlier',
                    'path': '/tmp/previewed.json',
                },
                {
                    'step_id': 'eval:bad:eval-threshold-failure',
                    'status': 'applied',
                    'created_at': 'later',
                    'path': '/tmp/applied.json',
                    'note': 'ran full eval',
                },
            ],
        )
        text = remediation_plan_markdown(tracked)

        self.assertFalse(tracked['ok'])
        self.assertEqual(tracked['applied_count'], 1)
        self.assertEqual(tracked['previewed_count'], 0)
        self.assertEqual(tracked['steps'][0]['execution']['status'], 'applied')
        self.assertEqual(tracked['steps'][0]['execution']['event_path'], '/tmp/applied.json')
        self.assertIn('| 1 | high | eval | applied | Eval failed |', text)

    def test_remediation_execution_tracking_escalates_stale_applied_steps(self) -> None:
        plan = {
            'ok': False,
            'step_count': 1,
            'high_count': 0,
            'medium_count': 1,
            'steps': [
                {
                    'rank': 1,
                    'id': 'preflight:risk',
                    'severity': 'medium',
                    'kind': 'work_session',
                    'summary': 'Preflight risk remains',
                    'seen_snapshot_count': 3,
                    'why': 'Refresh work session.',
                }
            ],
        }

        tracked = apply_remediation_execution_tracking(
            plan,
            [{'step_id': 'preflight:risk', 'status': 'applied', 'created_at': 'later', 'path': '/tmp/applied.json'}],
        )
        text = remediation_plan_markdown(tracked)

        self.assertEqual(tracked['stale_applied_count'], 1)
        self.assertEqual(tracked['high_count'], 1)
        self.assertEqual(tracked['medium_count'], 0)
        self.assertEqual(tracked['steps'][0]['severity'], 'high')
        self.assertTrue(tracked['steps'][0]['stale_after_apply'])
        self.assertEqual(tracked['steps'][0]['execution']['status'], 'stale_applied')
        self.assertEqual(tracked['steps'][0]['execution']['event_status'], 'applied')
        self.assertIn('Stale after apply: 1', text)
        self.assertIn('| 1 | high | work_session | stale_applied | Preflight risk remains |', text)

    def test_remediation_execution_tracking_keeps_single_snapshot_apply_applied(self) -> None:
        plan = {
            'ok': False,
            'step_count': 1,
            'high_count': 0,
            'medium_count': 1,
            'steps': [
                {
                    'rank': 1,
                    'id': 'preflight:risk',
                    'severity': 'medium',
                    'kind': 'work_session',
                    'summary': 'Preflight risk',
                    'seen_snapshot_count': 1,
                }
            ],
        }

        tracked = apply_remediation_execution_tracking(
            plan,
            [{'step_id': 'preflight:risk', 'status': 'applied', 'created_at': 'later'}],
        )

        self.assertEqual(tracked['applied_count'], 1)
        self.assertEqual(tracked['stale_applied_count'], 0)
        self.assertEqual(tracked['steps'][0]['execution']['status'], 'applied')
        self.assertEqual(tracked['steps'][0]['severity'], 'medium')

    def test_remediation_execution_tracking_resolved_plan_is_ok(self) -> None:
        plan = {
            'ok': False,
            'step_count': 1,
            'high_count': 0,
            'medium_count': 1,
            'steps': [{'rank': 1, 'id': 'preflight:risk', 'severity': 'medium', 'kind': 'work_session'}],
        }

        tracked = apply_remediation_execution_tracking(
            plan,
            [{'step_id': 'preflight:risk', 'status': 'resolved', 'created_at': 'done'}],
        )

        self.assertTrue(tracked['ok'])
        self.assertEqual(tracked['resolved_count'], 1)
        self.assertEqual(tracked['pending_count'], 0)

    def test_write_and_load_remediation_execution_event_preserves_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard = build_dashboard(
                [],
                [
                    {
                        'id': 'eval-bad',
                        'ok': False,
                        'labels': {'fail': 1},
                        'threshold_failure_count': 1,
                        'report_path': '/tmp/eval.md',
                    }
                ],
                [],
            )
            step_id = dashboard['remediation_plan']['steps'][0]['id']
            path = write_remediation_execution_event(
                root,
                step_id=step_id,
                status='previewed',
                note='checked command',
                dashboard=dashboard,
            )
            events = load_remediation_execution_events(root)

        self.assertEqual(events[0]['path'], str(path))
        self.assertEqual(events[0]['step_id'], step_id)
        self.assertEqual(events[0]['status'], 'previewed')
        self.assertEqual(events[0]['note'], 'checked command')
        self.assertEqual(events[0]['step_context']['id'], step_id)

    def test_action_snapshot_round_trip_loads_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard = build_dashboard(
                [{'id': 'preflight-risk', 'ok': True, 'medium_count': 1, 'report_path': '/tmp/preflight.md'}],
                [],
                [],
            )
            path = write_action_snapshot(root, dashboard)

            loaded = load_latest_action_snapshot(root)
            loaded_many = load_action_snapshots(root)

        self.assertEqual(loaded['path'], str(path))
        self.assertEqual(loaded_many[0]['path'], str(path))
        self.assertEqual(loaded['action_count'], 1)
        self.assertEqual(loaded['actions'][0]['id'], dashboard['actions'][0]['id'])


if __name__ == '__main__':
    unittest.main()
