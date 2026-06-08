from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.research_ci_check import build_ci_check, ci_markdown, make_ci_dir, refresh_quality_timeline, run_fixture_eval


class ResearchCiCheckTests(unittest.TestCase):
    def test_make_ci_dir_creates_unique_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = make_ci_dir(root)
            second = make_ci_dir(root)

        self.assertNotEqual(first, second)
        self.assertTrue(first.name)
        self.assertTrue(second.name)

    def test_run_fixture_eval_parses_summary_and_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eval_dir = root / 'fixture_eval'
            eval_dir.mkdir()
            (eval_dir / 'summary.json').write_text(
                '{"task_count": 1, "average_score": 82, "labels": {"pass": 1}, "thresholds": {"ok": true}}',
                encoding='utf-8',
            )
            with patch('scripts.research_ci_check.subprocess.run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = 'ok'
                run.return_value.stderr = ''

                result = run_fixture_eval(
                    output_dir=root,
                    tasks=root / 'tasks.json',
                    fixture=root / 'fixture.json',
                    min_score=40,
                    min_average_score=None,
                    fail_on_labels=['fail'],
                )

        self.assertTrue(result['ok'])
        self.assertEqual(result['summary']['average_score'], 82)
        self.assertIn('--fixture', result['command'])
        self.assertIn('--fail-on-label', result['command'])

    def test_build_ci_check_combines_stack_and_eval_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.research_ci_check.build_status',
                return_value={
                    'ok': True,
                    'prompt': {'ok': True},
                    'docs': {'ok': True},
                    'config': {'ok': True},
                    'runs': {'total_runs': 0, 'archive_candidates': 0, 'status_counts': {}, 'latest_budget_totals': {}},
                    'tools': {'ok': True, 'tool_count': 30, 'missing_tools': [], 'unexpected_tools': []},
                },
            ), patch(
                'scripts.research_ci_check.run_fixture_eval',
                return_value={
                    'ok': True,
                    'returncode': 0,
                    'summary_path': '/tmp/eval/summary.json',
                    'summary_md_path': '/tmp/eval/summary.md',
                    'summary': {'task_count': 2, 'average_score': 90, 'labels': {'pass': 2}, 'thresholds': {'ok': True}},
                },
            ), patch(
                'scripts.research_ci_check.run_remediation_benchmark',
                return_value={'ok': True, 'scenario_count': 4, 'passed': 4, 'failed': 0, 'path': '/tmp/remediation.json'},
            ):
                check = build_ci_check(
                    output_dir=root,
                    config_path=root / 'mcp.json',
                    research_dir=root,
                    runs_root=root / 'runs',
                    probe_tools=True,
                    run_eval=True,
                    run_remediation_benchmark_check=True,
                    tasks=root / 'tasks.json',
                    fixture=root / 'fixture.json',
                    min_score=40,
                    min_average_score=None,
                    fail_on_labels=['fail'],
                )

        self.assertTrue(check['ok'])
        self.assertTrue(check['probe_tools'])
        self.assertTrue(check['run_eval'])
        self.assertEqual(check['fixture_eval']['summary']['task_count'], 2)
        self.assertTrue(check['run_remediation_benchmark'])
        self.assertEqual(check['remediation_learning_benchmark']['scenario_count'], 4)

    def test_build_ci_check_fails_when_eval_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('scripts.research_ci_check.run_fixture_eval', return_value={'ok': False, 'returncode': 1, 'summary': {}}):
                check = build_ci_check(
                    output_dir=root,
                    config_path=root / 'mcp.json',
                    research_dir=root,
                    runs_root=root / 'runs',
                    probe_tools=False,
                    run_eval=True,
                    run_remediation_benchmark_check=False,
                    tasks=root / 'tasks.json',
                    fixture=root / 'fixture.json',
                    min_score=40,
                    min_average_score=None,
                    fail_on_labels=['fail'],
                )

        self.assertFalse(check['ok'])

    def test_refresh_quality_timeline_writes_markdown_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ci_dir = root / 'ci-1'
            ci_dir.mkdir()
            (ci_dir / 'ci_check.json').write_text(
                '{"ok": true, "completed_at": "now", "stack": {"ok": true}, "fixture_eval": {"ok": true, "summary": {"average_score": 90, "labels": {"pass": 1}}}, "remediation_learning_benchmark": {"ok": true, "failed": 0}}',
                encoding='utf-8',
            )
            output = root / 'quality.md'

            result = refresh_quality_timeline(ci_root=root, output=output, limit=5)
            output_exists = output.exists()
            json_exists = output.with_suffix('.json').exists()
            output_text = output.read_text(encoding='utf-8')

        self.assertTrue(result['ok'])
        self.assertEqual(result['event_count'], 1)
        self.assertTrue(output_exists)
        self.assertTrue(json_exists)
        self.assertIn('Research Quality Timeline', output_text)

    def test_ci_markdown_includes_stack_and_eval_paths(self) -> None:
        text = ci_markdown(
            {
                'ok': True,
                'completed_at': 'now',
                'output_dir': '/tmp/ci',
                'probe_tools': True,
                'run_eval': True,
                'run_remediation_benchmark': True,
                'stack': {
                    'ok': True,
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
                    'tools': {'ok': True, 'tool_count': 30, 'missing_tools': [], 'unexpected_tools': []},
                },
                'fixture_eval': {
                    'ok': True,
                    'returncode': 0,
                    'summary_path': '/tmp/eval/summary.json',
                    'summary_md_path': '/tmp/eval/summary.md',
                    'summary': {'task_count': 2, 'average_score': 88, 'labels': {'pass': 2}, 'thresholds': {'ok': True}},
                },
                'remediation_learning_benchmark': {
                    'ok': True,
                    'scenario_count': 4,
                    'passed': 4,
                    'failed': 0,
                    'path': '/tmp/remediation.json',
                },
                'refresh_quality_timeline': True,
                'quality_timeline': {
                    'ok': True,
                    'output': '/tmp/quality_timeline.md',
                    'json': '/tmp/quality_timeline.json',
                    'event_count': 3,
                    'failure_count': 0,
                    'regression_count': 1,
                },
            }
        )

        self.assertIn('# Research CI Check', text)
        self.assertIn('Status: pass', text)
        self.assertIn('/tmp/eval/summary.md', text)
        self.assertIn('/tmp/remediation.json', text)
        self.assertIn('/tmp/quality_timeline.md', text)
        self.assertIn('Regressions: 1', text)
        self.assertIn('Research stack status: OK', text)


if __name__ == '__main__':
    unittest.main()
