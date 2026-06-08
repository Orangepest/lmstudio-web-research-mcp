from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from scripts.run_research_eval import (
    ROOT,
    build_threshold_report,
    _load_fixture,
    make_output_dir,
    parse_bool,
    run_task,
    summary_markdown,
    task_with_profile_defaults,
)
from web_research.eval import load_eval_tasks
from web_research.profiles import get_work_profile


class RunResearchEvalScriptTests(unittest.TestCase):
    def test_parse_bool_handles_string_values(self) -> None:
        self.assertFalse(parse_bool('false'))
        self.assertFalse(parse_bool('0'))
        self.assertFalse(parse_bool('off'))
        self.assertTrue(parse_bool('true'))
        self.assertTrue(parse_bool('1'))
        self.assertTrue(parse_bool('yes'))

    def test_parse_bool_rejects_ambiguous_values(self) -> None:
        with self.assertRaises(ValueError):
            parse_bool('sometimes')

    def test_make_output_dir_creates_unique_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = make_output_dir(root)
            second = make_output_dir(root)

        self.assertNotEqual(first, second)
        self.assertTrue(first.name)
        self.assertTrue(second.name)

    def test_regression_eval_task_file_loads(self) -> None:
        tasks = load_eval_tasks(ROOT / 'evals' / 'research_regression_tasks.json')

        self.assertGreaterEqual(len(tasks), 4)
        self.assertTrue(all(task['id'].startswith('regression-') for task in tasks))
        self.assertTrue(any(task['category'] == 'freshness_current_lookup' for task in tasks))
        self.assertTrue(any('claim_support_present' in task['required_checks'] for task in tasks))

    def test_fixture_eval_task_file_loads(self) -> None:
        tasks = load_eval_tasks(ROOT / 'evals' / 'research_fixture_tasks.json')
        fixture = _load_fixture(ROOT / 'evals' / 'fixtures' / 'ci_basic.json')

        self.assertEqual(len(tasks), 5)
        self.assertEqual(fixture['id'], 'ci_basic')
        self.assertIn(tasks[0]['question'], fixture['searches'])
        forced = next(task for task in tasks if task['id'] == 'fixture-forced-conflicted-browser-actions')
        self.assertIn('contradiction_table_rows_present', forced['required_checks'])
        self.assertIn(forced['question'], fixture['searches'])
        noisy = next(task for task in tasks if task['id'] == 'fixture-noisy-source-selection')
        self.assertIn('source_selection_avoids_low_value', noisy['required_checks'])
        self.assertIn('source_selection_reads_buried_strong_sources', noisy['required_checks'])
        self.assertIn(noisy['question'], fixture['searches'])
        deep = next(task for task in tasks if task['id'] == 'fixture-deep-contradiction-follow-up')
        self.assertEqual(deep['tool'], 'deep_research')
        self.assertIn('contradiction_resolution_searched', deep['required_checks'])
        self.assertIn(deep['question'], fixture['searches'])

    def test_run_task_uses_fixture_without_live_web(self) -> None:
        tasks = load_eval_tasks(ROOT / 'evals' / 'research_fixture_tasks.json')
        fixture = _load_fixture(ROOT / 'evals' / 'fixtures' / 'ci_basic.json')

        payload, record = asyncio.run(run_task(tasks[0], fixture=fixture))

        self.assertTrue(payload['ok'])
        self.assertTrue(payload['eval_fixture']['enabled'])
        self.assertEqual(payload['search']['provider'], 'fixture')
        self.assertGreaterEqual(len(payload['sources']), 2)
        self.assertTrue(payload['claim_support']['ok'])
        self.assertIn('## Best Evidence', payload['final_report'])
        self.assertGreaterEqual(record['score']['metrics']['indexed_supported_claim_count'], 1)

    def test_run_task_fixture_forces_contradiction_table_rows(self) -> None:
        tasks = load_eval_tasks(ROOT / 'evals' / 'research_fixture_tasks.json')
        task = next(item for item in tasks if item['id'] == 'fixture-forced-conflicted-browser-actions')
        fixture = _load_fixture(ROOT / 'evals' / 'fixtures' / 'ci_basic.json')

        payload, record = asyncio.run(run_task(task, fixture=fixture))
        metrics = record['score']['metrics']

        self.assertTrue(payload['ok'])
        self.assertGreaterEqual(metrics['conflicted_claim_count'], 1)
        self.assertGreaterEqual(metrics['contradiction_table_row_count'], 1)
        self.assertGreaterEqual(metrics['contradiction_table_supporting_row_count'], 1)
        self.assertGreaterEqual(metrics['contradiction_table_conflicting_row_count'], 1)
        self.assertGreaterEqual(metrics['contradiction_table_resolution_query_count'], 1)
        self.assertTrue(record['score']['checks']['contradiction_table_rows_present'])
        self.assertTrue(record['score']['checks']['contradiction_table_source_pairs_present'])
        self.assertIn('## Source-Claim Contradiction Table', payload['final_report'])

    def test_run_task_fixture_selects_buried_sources_from_noisy_results(self) -> None:
        tasks = load_eval_tasks(ROOT / 'evals' / 'research_fixture_tasks.json')
        task = next(item for item in tasks if item['id'] == 'fixture-noisy-source-selection')
        fixture = _load_fixture(ROOT / 'evals' / 'fixtures' / 'ci_basic.json')

        payload, record = asyncio.run(run_task(task, fixture=fixture))
        metrics = record['score']['metrics']
        selected_urls = [source['final_url'] for source in payload['sources']]

        self.assertTrue(payload['ok'])
        self.assertEqual(
            set(selected_urls),
            {
                'https://docs.example.com/browser-automation-safety-setup',
                'https://agency.gov/ai/browser-agent-security-policy',
            },
        )
        self.assertEqual(set(metrics['selected_original_ranks']), {4, 5})
        self.assertEqual(metrics['selected_low_value_source_count'], 0)
        self.assertGreaterEqual(metrics['planned_low_value_source_count'], 1)
        self.assertEqual(metrics['buried_strong_selected_count'], 2)
        self.assertTrue(record['score']['checks']['source_selection_avoids_low_value'])
        self.assertTrue(record['score']['checks']['source_selection_reads_buried_strong_sources'])
        self.assertEqual(record['score']['required_check_failures'], [])

    def test_run_task_deep_fixture_executes_contradiction_resolution_search(self) -> None:
        tasks = load_eval_tasks(ROOT / 'evals' / 'research_fixture_tasks.json')
        task = next(item for item in tasks if item['id'] == 'fixture-deep-contradiction-follow-up')
        fixture = _load_fixture(ROOT / 'evals' / 'fixtures' / 'ci_basic.json')

        payload, record = asyncio.run(run_task(task, fixture=fixture))
        metrics = record['score']['metrics']
        decisions = payload['agent_loop']['decisions']

        self.assertTrue(payload['ok'])
        self.assertGreaterEqual(metrics['conflicted_claim_count'], 1)
        self.assertGreaterEqual(metrics['contradiction_table_row_count'], 1)
        self.assertGreaterEqual(metrics['contradiction_resolution_search_count'], 1)
        self.assertTrue(record['score']['checks']['contradiction_resolution_searched'])
        self.assertTrue(record['score']['checks']['follow_up_rounds_recorded'])
        self.assertTrue(
            any(
                item.get('decision') == 'searched_follow_up_query'
                and item.get('intent') == 'contradiction_resolution'
                and item.get('new_source_count', 0) > 0
                for item in decisions
            )
        )

    def test_summary_markdown_includes_audit_metrics(self) -> None:
        text = summary_markdown(
            {
                'completed_at': 'now',
                'task_count': 1,
                'average_score': 80,
                'output_dir': '/tmp/eval',
                'weakest_checks': [{'check': 'claim_support_present', 'count': 1}],
                'records': [
                    {
                        'task': {'id': 'regression-one', 'category': 'coverage_primary_source'},
                        'score': {
                            'label': 'pass',
                            'score': 88,
                            'metrics': {
                                'source_count': 3,
                                'unique_domain_count': 2,
                                'research_quality_label': 'strong',
                                'coverage_missing_intent_count': 0,
                                'primary_source_count': 2,
                                'freshness_gap_count': 0,
                                'conflicted_claim_count': 1,
                                'contradiction_table_row_count': 1,
                                'contradiction_table_resolution_query_count': 1,
                            },
                        },
                    }
                ],
            }
        )

        self.assertIn('Claim Support', text)
        self.assertIn('Intent Quality', text)
        self.assertIn('Contradiction Table', text)
        self.assertIn('1 conflicted / 1 rows / 1 queries', text)
        self.assertIn('## Common Weaknesses', text)
        self.assertIn('| regression-one |', text)

    def test_build_threshold_report_fails_low_scores_and_labels(self) -> None:
        report = build_threshold_report(
            [
                {'task': {'id': 'good'}, 'score': {'label': 'pass', 'score': 90}},
                {'task': {'id': 'bad'}, 'score': {'label': 'fail', 'score': 45}},
            ],
            average_score=67.5,
            min_score=60,
            min_average_score=70,
            fail_on_labels=['fail'],
        )

        self.assertFalse(report['ok'])
        self.assertEqual(report['failure_count'], 3)
        self.assertEqual(report['fail_on_labels'], ['fail'])
        self.assertTrue(any(failure['type'] == 'average_score' for failure in report['failures']))
        self.assertTrue(any(failure['type'] == 'task_score' and failure['task_id'] == 'bad' for failure in report['failures']))
        self.assertTrue(any(failure['type'] == 'task_label' and failure['task_id'] == 'bad' for failure in report['failures']))

    def test_task_with_profile_defaults_applies_eval_depth_without_overriding_task_params(self) -> None:
        profile = get_work_profile('exhaustive')
        task = {
            'id': 'depth',
            'tool': 'deep_research',
            'question': 'What changed?',
            'params': {'breadth': 2, 'freshness': 'month'},
        }

        merged = task_with_profile_defaults(task, profile)

        self.assertEqual(merged['profile'], 'exhaustive')
        self.assertEqual(merged['params']['breadth'], 2)
        self.assertEqual(merged['params']['freshness'], 'month')
        self.assertEqual(merged['params']['read_top_per_query'], profile.read_top_per_query)
        self.assertEqual(merged['params']['follow_up_rounds'], profile.follow_up_rounds)
        self.assertEqual(merged['params']['report_format'], 'long_report')
        self.assertTrue(merged['params']['render'])
        self.assertNotIn('profile', task)

    def test_task_with_profile_defaults_supports_research_web_tasks(self) -> None:
        profile = get_work_profile('careful')
        task = {'id': 'web', 'question': 'Find sources', 'params': {'read_top': 5}}

        merged = task_with_profile_defaults(task, profile)

        self.assertEqual(merged['profile'], 'careful')
        self.assertEqual(merged['params']['read_top'], 5)
        self.assertEqual(merged['params']['max_results'], 8)
        self.assertEqual(merged['params']['report_format'], profile.report_format)

    def test_summary_markdown_includes_threshold_failures(self) -> None:
        text = summary_markdown(
            {
                'completed_at': 'now',
                'task_count': 0,
                'average_score': 0,
                'output_dir': '/tmp/eval',
                'records': [],
                'thresholds': {
                    'ok': False,
                    'min_score': 70,
                    'min_average_score': 80,
                    'fail_on_labels': ['fail'],
                    'failure_count': 1,
                    'failures': [{'message': 'regression-one scored 50/100 below threshold 70/100.'}],
                },
            }
        )

        self.assertIn('## Thresholds', text)
        self.assertIn('- Status: fail', text)
        self.assertIn('regression-one scored 50/100 below threshold 70/100.', text)


if __name__ == '__main__':
    unittest.main()
