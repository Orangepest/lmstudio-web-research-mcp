from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.compare_eval_runs import compare_eval_runs, compare_latest_eval_runs, comparison_markdown, list_eval_summaries


def _record(
    task_id: str,
    score: int,
    *,
    weakest: list[str] | None = None,
    sources: int = 2,
    claim_support: int = 1,
    contradiction_rows: int = 0,
    buried_strong: int = 0,
    low_value: int = 0,
) -> dict:
    return {
        'task': {'id': task_id, 'category': 'test'},
        'score': {
            'label': 'pass' if score >= 80 else 'borderline' if score >= 60 else 'fail',
            'score': score,
            'weakest_checks': weakest or [],
            'metrics': {
                'source_count': sources,
                'primary_source_count': 1 if sources else 0,
                'indexed_supported_claim_count': claim_support,
                'indexed_unsupported_claim_count': 0,
                'average_intent_quality_score': 75,
                'contradiction_resolution_search_count': 0,
                'contradiction_table_row_count': contradiction_rows,
                'contradiction_table_resolution_query_count': contradiction_rows,
                'buried_strong_selected_count': buried_strong,
                'selected_low_value_source_count': low_value,
                'planned_low_value_source_count': low_value,
            },
        },
    }


def _write_summary(root: Path, run_id: str, *, completed_at: str, records: list[dict]) -> Path:
    run_dir = root / run_id
    run_dir.mkdir()
    scores = [record['score']['score'] for record in records]
    weakness_counts: dict[str, int] = {}
    for record in records:
        for check in record['score'].get('weakest_checks', []):
            weakness_counts[check] = weakness_counts.get(check, 0) + 1
    summary = {
        'ok': True,
        'id': run_id,
        'completed_at': completed_at,
        'task_count': len(records),
        'average_score': round(sum(scores) / len(scores), 1) if scores else 0,
        'labels': {'pass': sum(1 for score in scores if score >= 80)},
        'weakest_checks': [{'check': check, 'count': count} for check, count in sorted(weakness_counts.items())],
        'records': records,
    }
    path = run_dir / 'summary.json'
    path.write_text(json.dumps(summary), encoding='utf-8')
    return path


class CompareEvalRunsTests(unittest.TestCase):
    def test_compare_eval_runs_reports_score_and_quality_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = _write_summary(
                root,
                'base',
                completed_at='2026-06-01T00:00:00Z',
                records=[
                    _record('task-a', 80, weakest=['claim_support_present'], sources=2, claim_support=1, contradiction_rows=0),
                    _record('task-b', 75, weakest=['intent_quality_adequate'], sources=1, claim_support=0),
                ],
            )
            compare = _write_summary(
                root,
                'compare',
                completed_at='2026-06-02T00:00:00Z',
                records=[
                    _record('task-a', 90, weakest=[], sources=3, claim_support=2, contradiction_rows=1, buried_strong=2),
                    _record('task-b', 65, weakest=['intent_quality_adequate', 'citation_audit_passes'], sources=1, claim_support=0),
                    _record('task-c', 70, weakest=['matches_expected_domains'], sources=2, claim_support=1),
                ],
            )

            result = compare_eval_runs(base, compare)

        self.assertTrue(result['ok'])
        self.assertEqual(result['delta']['average_score'], -2.5)
        self.assertEqual(result['delta']['regression_count'], 1)
        self.assertEqual(result['delta']['improvement_count'], 1)
        self.assertEqual(result['delta']['added_task_count'], 1)
        task_a = next(item for item in result['task_deltas'] if item['task_id'] == 'task-a')
        self.assertEqual(task_a['score_delta'], 10)
        self.assertEqual(task_a['source_delta'], 1)
        self.assertEqual(task_a['claim_support_delta'], 1)
        self.assertEqual(task_a['contradiction_table_row_delta'], 1)
        self.assertEqual(task_a['contradiction_table_resolution_query_delta'], 1)
        self.assertEqual(task_a['buried_strong_selected_delta'], 2)
        self.assertEqual(result['delta']['buried_strong_selected_delta'], 2)
        self.assertEqual(result['delta']['selected_low_value_source_delta'], 0)
        self.assertIn('claim_support_present', task_a['resolved_weakest_checks'])
        task_b = next(item for item in result['task_deltas'] if item['task_id'] == 'task-b')
        self.assertIn('citation_audit_passes', task_b['new_weakest_checks'])
        self.assertTrue(any(item['check'] == 'citation_audit_passes' and item['delta'] == 1 for item in result['weakness_deltas']))

    def test_compare_latest_eval_runs_uses_newest_two_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_summary(root, 'old', completed_at='2026-06-01T00:00:00Z', records=[_record('task', 60)])
            _write_summary(root, 'mid', completed_at='2026-06-02T00:00:00Z', records=[_record('task', 70)])
            _write_summary(root, 'new', completed_at='2026-06-03T00:00:00Z', records=[_record('task', 85)])

            listed = list_eval_summaries(root)
            result = compare_latest_eval_runs(root)

        self.assertEqual([item['id'] for item in listed[:2]], ['new', 'mid'])
        self.assertEqual(result['base']['id'], 'mid')
        self.assertEqual(result['compare']['id'], 'new')
        self.assertEqual(result['delta']['average_score'], 15.0)

    def test_comparison_markdown_includes_deltas_and_weaknesses(self) -> None:
        text = comparison_markdown(
            {
                'ok': True,
                'base': {'id': 'base', 'average_score': 70},
                'compare': {'id': 'compare', 'average_score': 82},
                'delta': {
                    'average_score': 12,
                    'regression_count': 0,
                    'improvement_count': 1,
                    'added_task_count': 0,
                    'removed_task_count': 0,
                },
                'task_deltas': [
                    {
                        'task_id': 'task-a',
                        'base_score': 70,
                        'compare_score': 82,
                        'score_delta': 12,
                        'source_delta': 1,
                        'primary_source_delta': 1,
                        'claim_support_delta': 1,
                        'buried_strong_selected_delta': 2,
                        'selected_low_value_source_delta': -1,
                        'contradiction_table_row_delta': 1,
                        'contradiction_table_resolution_query_delta': 1,
                        'intent_quality_delta': 5,
                        'new_weakest_checks': [],
                        'resolved_weakest_checks': ['claim_support_present'],
                    }
                ],
                'weakness_deltas': [{'check': 'claim_support_present', 'base_count': 1, 'compare_count': 0, 'delta': -1}],
            }
        )

        self.assertIn('# Eval Trend Comparison', text)
        self.assertIn('| task-a |', text)
        self.assertIn('+2 buried / -1 low-value', text)
        self.assertIn('+1 rows / +1 queries', text)
        self.assertIn('resolved: claim_support_present', text)
        self.assertIn('claim_support_present: 1 -> 0 (-1)', text)


if __name__ == '__main__':
    unittest.main()
