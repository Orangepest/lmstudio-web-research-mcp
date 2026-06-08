from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.compare_research_runs import compare_research_chain, compare_research_runs, comparison_markdown
from web_research.runs import save_research_run


class CompareResearchRunsTests(unittest.TestCase):
    def _set_created_at(self, runs_root: Path, run_id: str, value: str) -> None:
        for filename in ('run.json', 'summary.json'):
            path = runs_root / run_id / filename
            payload = json.loads(path.read_text(encoding='utf-8'))
            if filename == 'run.json':
                payload['run']['created_at'] = value
                payload['run']['updated_at'] = value
            else:
                payload['created_at'] = value
                payload['updated_at'] = value
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_compare_research_runs_reports_deltas_and_resolved_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = save_research_run(
                'research_web',
                'compare topic',
                {
                    'ok': True,
                    'sources': [{'source_id': 1, 'final_url': 'https://docs.example.com/a'}],
                    'claims': [{'claim': 'Base claim.'}],
                    'evidence': [{'quote': 'Base evidence.'}],
                    'research_quality': {'score': 55},
                    'research_coverage': {'missing_intents': ['primary_source']},
                    'source_freshness': {'gaps': ['No recent-change evidence snippets were extracted.']},
                },
                root=root / 'runs',
            )
            follow_up = save_research_run(
                'research_web',
                'compare topic follow up',
                {
                    'ok': True,
                    'sources': [
                        {'source_id': 1, 'final_url': 'https://docs.example.com/a'},
                        {'source_id': 2, 'final_url': 'https://agency.gov/b'},
                    ],
                    'claims': [{'claim': 'Base claim.'}, {'claim': 'Follow-up claim.'}],
                    'evidence': [{'quote': 'Base evidence.'}, {'quote': 'Follow-up evidence.'}],
                    'research_quality': {'score': 75},
                    'research_coverage': {'missing_intents': []},
                    'source_freshness': {'gaps': []},
                },
                parent_run_id=base['run_id'],
                root=root / 'runs',
            )

            result = compare_research_runs(base['run_id'], follow_up['run_id'], runs_root=root / 'runs')

            self.assertTrue(result['ok'])
            self.assertEqual(result['delta']['sources'], 1)
            self.assertEqual(result['delta']['domains'], 1)
            self.assertEqual(result['delta']['claims'], 1)
            self.assertEqual(result['delta']['research_score'], 20)
            self.assertEqual(result['new_domains'], ['agency.gov'])
            self.assertEqual(result['resolved_missing_intents'], ['primary_source'])
            self.assertEqual(result['resolved_freshness_gaps'], ['No recent-change evidence snippets were extracted.'])

    def test_compare_research_chain_orders_parent_to_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = save_research_run('research_web', 'parent topic', {'ok': True}, root=root / 'runs')
            child = save_research_run('research_web', 'child topic', {'ok': True}, parent_run_id=parent['run_id'], root=root / 'runs')

            result = compare_research_chain(child['run_id'], runs_root=root / 'runs')
            markdown = comparison_markdown(result)

            self.assertTrue(result['ok'])
            self.assertEqual([item['run_id'] for item in result['chain']], [parent['run_id'], child['run_id']])
            self.assertEqual(result['comparison_count'], 1)
            self.assertIn('# Research Run Chain', markdown)
            self.assertIn(f"{parent['run_id']} -> {child['run_id']}", markdown)

    def test_compare_research_chain_can_start_from_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = save_research_run('research_web', 'parent topic', {'ok': True}, root=root / 'runs')
            child = save_research_run('research_web', 'child topic', {'ok': True}, parent_run_id=parent['run_id'], root=root / 'runs')

            result = compare_research_chain(parent['run_id'], runs_root=root / 'runs')

            self.assertTrue(result['ok'])
            self.assertEqual([item['run_id'] for item in result['chain']], [parent['run_id'], child['run_id']])
            self.assertEqual(result['comparison_count'], 1)

    def test_compare_research_chain_finds_children_beyond_latest_100_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / 'runs'
            parent = save_research_run('research_web', 'old parent topic', {'ok': True}, root=runs_root)
            child = save_research_run('research_web', 'old child topic', {'ok': True}, parent_run_id=parent['run_id'], root=runs_root)
            self._set_created_at(runs_root, parent['run_id'], '2000-01-01T00:00:00Z')
            self._set_created_at(runs_root, child['run_id'], '2000-01-02T00:00:00Z')
            for index in range(105):
                save_research_run('research_web', f'new unrelated topic {index}', {'ok': True}, root=runs_root)

            result = compare_research_chain(parent['run_id'], runs_root=runs_root)

            self.assertTrue(result['ok'])
            self.assertEqual([item['run_id'] for item in result['chain']], [parent['run_id'], child['run_id']])
            self.assertEqual(result['comparison_count'], 1)

    def test_compare_research_runs_preserves_query_strings_in_source_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = save_research_run(
                'research_web',
                'query source topic',
                {'ok': True, 'sources': [{'source_id': 1, 'final_url': 'https://example.com/search?q=alpha'}]},
                root=root / 'runs',
            )
            follow_up = save_research_run(
                'research_web',
                'query source topic follow up',
                {'ok': True, 'sources': [{'source_id': 1, 'final_url': 'https://example.com/search?q=beta'}]},
                parent_run_id=base['run_id'],
                root=root / 'runs',
            )

            result = compare_research_runs(base['run_id'], follow_up['run_id'], runs_root=root / 'runs')

            self.assertTrue(result['ok'])
            self.assertEqual(result['new_source_keys'], ['example.com/search?q=beta'])
            self.assertEqual(result['removed_source_keys'], ['example.com/search?q=alpha'])


if __name__ == '__main__':
    unittest.main()
