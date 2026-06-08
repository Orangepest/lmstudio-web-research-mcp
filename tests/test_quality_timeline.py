from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.quality_timeline import collect_quality_timeline, timeline_markdown


class QualityTimelineTests(unittest.TestCase):
    def test_collect_quality_timeline_summarizes_ci_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / '20260606T090000Z-old'
            older.mkdir()
            (older / 'ci_check.md').write_text('# old\n', encoding='utf-8')
            (older / 'ci_check.json').write_text(
                json.dumps(
                    {
                        'ok': True,
                        'completed_at': '2026-06-06T09:00:00Z',
                        'stack': {
                            'ok': True,
                            'prompt': {'ok': True},
                            'docs': {'ok': True},
                            'config': {'ok': True},
                            'tools': {'tool_count': 2, 'missing_tools': [], 'unexpected_tools': []},
                            'search': {'ok': True, 'recent_failures': {'count': 0}},
                        },
                        'fixture_eval': {
                            'ok': True,
                            'returncode': 0,
                            'summary_path': '/tmp/eval-old/summary.json',
                            'summary_md_path': '/tmp/eval-old/summary.md',
                            'summary': {
                                'task_count': 4,
                                'average_score': 92,
                                'labels': {'pass': 4},
                                'thresholds': {'ok': True, 'failure_count': 0},
                            },
                        },
                        'remediation_learning_benchmark': {
                            'ok': True,
                            'scenario_count': 4,
                            'passed': 4,
                            'failed': 0,
                            'records': [],
                            'path': '/tmp/rem-old.json',
                        },
                    }
                ),
                encoding='utf-8',
            )
            latest = root / '20260606T091000Z-new'
            latest.mkdir()
            (latest / 'ci_check.md').write_text('# new\n', encoding='utf-8')
            (latest / 'ci_check.json').write_text(
                json.dumps(
                    {
                        'ok': False,
                        'completed_at': '2026-06-06T09:10:00Z',
                        'stack': {
                            'ok': True,
                            'prompt': {'ok': True},
                            'docs': {'ok': True},
                            'config': {'ok': True},
                            'tools': {'tool_count': 2, 'missing_tools': [], 'unexpected_tools': []},
                            'search': {'ok': True, 'recent_failures': {'count': 1}},
                        },
                        'fixture_eval': {
                            'ok': True,
                            'returncode': 0,
                            'summary_path': '/tmp/eval-new/summary.json',
                            'summary_md_path': '/tmp/eval-new/summary.md',
                            'summary': {
                                'task_count': 4,
                                'average_score': 79,
                                'labels': {'borderline': 3, 'fail': 1},
                                'thresholds': {'ok': False, 'failure_count': 1},
                            },
                        },
                        'remediation_learning_benchmark': {
                            'ok': False,
                            'scenario_count': 4,
                            'passed': 3,
                            'failed': 1,
                            'records': [{'id': 'missing-primary', 'ok': False}],
                            'path': '/tmp/rem-new.json',
                        },
                    }
                ),
                encoding='utf-8',
            )

            timeline = collect_quality_timeline(root)

        self.assertFalse(timeline['ok'])
        self.assertEqual(timeline['event_count'], 2)
        self.assertEqual(timeline['failure_count'], 1)
        self.assertEqual(timeline['regression_count'], 1)
        latest_event = timeline['events'][0]
        self.assertEqual(latest_event['id'], '20260606T091000Z-new')
        self.assertEqual(latest_event['fixture_eval_average_delta'], -13.0)
        self.assertEqual(latest_event['remediation_failed_delta'], 1)
        self.assertIn('eval_score_drop', latest_event['risk_flags'])
        self.assertIn('remediation_regression', latest_event['risk_flags'])
        self.assertIn('search_provider_failures', latest_event['risk_flags'])

    def test_timeline_markdown_renders_compact_table(self) -> None:
        timeline = {
            'ok': True,
            'event_count': 1,
            'total_event_count': 1,
            'failure_count': 0,
            'regression_count': 0,
            'latest_id': 'ci-1',
            'events': [
                {
                    'id': 'ci-1',
                    'ok': True,
                    'stack_ok': True,
                    'fixture_eval_ok': True,
                    'fixture_eval_average_score': 90,
                    'fixture_eval_average_delta': None,
                    'fixture_eval_labels': {'pass': 2},
                    'remediation_ok': True,
                    'remediation_failed': 0,
                    'remediation_failed_delta': None,
                    'risk_flags': [],
                    'report_path': '/tmp/ci_check.md',
                }
            ],
        }

        text = timeline_markdown(timeline)

        self.assertIn('# Research Quality Timeline', text)
        self.assertIn('| ci-1 | yes | yes | yes | 90 | n/a | pass:2 | yes | 0 | n/a | none |', text)
        self.assertIn('[/tmp/ci_check.md](/tmp/ci_check.md)', text)


if __name__ == '__main__':
    unittest.main()
