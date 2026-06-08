from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from web_research.index import build_research_index, search_research_index, write_research_index
from web_research.runs import save_research_run


ROOT = Path(__file__).resolve().parents[1]


class ResearchIndexTests(unittest.TestCase):
    def test_build_and_search_research_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run(
                'research_web',
                'vector search topic',
                {
                    'ok': True,
                    'final_report': 'Vector indexes help retrieve saved privacy research.',
                    'sources': [
                        {
                            'source_id': 1,
                            'title': 'Privacy Source',
                            'final_url': 'https://example.com/privacy',
                            'text': 'Privacy focused local AI assistants need offline retrieval indexes.',
                        }
                    ],
                    'claims': [{'claim_id': 1, 'claim': 'Local indexes improve recall for saved research.'}],
                },
                root=root / 'runs',
            )

            index = build_research_index(runs_root=root / 'runs')
            result = search_research_index(index, 'offline privacy retrieval index', limit=3)

            self.assertTrue(index['ok'])
            self.assertEqual(index['run_count'], 1)
            self.assertGreaterEqual(index['entry_count'], 3)
            self.assertTrue(result['matches'])
            self.assertEqual(result['matches'][0]['run_id'], saved['run_id'])
            self.assertIn(result['matches'][0]['item_type'], {'report', 'source', 'claim'})

    def test_write_research_index_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = {'ok': True, 'entries': [{'entry_id': 'one', 'vector': {'privacy': 1.0}}]}

            result = write_research_index(index, root / 'index.json')
            written = json.loads((root / 'index.json').read_text(encoding='utf-8'))

            self.assertTrue(result['ok'])
            self.assertEqual(written['entries'][0]['entry_id'], 'one')

    def test_research_index_cli_builds_and_searches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_research_run(
                'research_web',
                'coding assistant privacy',
                {'ok': True, 'final_report': 'Private coding assistants can use local saved research indexes.'},
                root=root / 'runs',
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts' / 'research_index.py'),
                    '--runs-root',
                    str(root / 'runs'),
                    '--index-path',
                    str(root / 'index.json'),
                    '--query',
                    'private coding assistant',
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertTrue(payload['ok'])
            self.assertTrue((root / 'index.json').exists())
            self.assertTrue(payload['search']['matches'])


if __name__ == '__main__':
    unittest.main()
