from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_source_pack import collect_source_pack, source_pack_markdown, write_source_pack
from web_research.runs import save_research_run


class BuildSourcePackTests(unittest.TestCase):
    def test_collect_source_pack_dedupes_sources_and_preserves_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = save_research_run(
                'research_web',
                'source pack alpha',
                {
                    'ok': True,
                    'sources': [
                        {
                            'source_id': 1,
                            'title': 'Shared Source',
                            'final_url': 'https://example.com/shared',
                            'reliability': {'source_type': 'documentation', 'reliability_weight': 'strong'},
                        }
                    ],
                    'claims': [{'claim_id': 1, 'claim': 'Shared source supports source packs.', 'supporting_sources': [1]}],
                    'evidence': [
                        {
                            'source_id': 1,
                            'citation': 'source:1[0:10]',
                            'url': 'https://example.com/shared',
                            'quote': 'Shared source supports source packs.',
                        }
                    ],
                },
                root=root / 'runs',
            )
            second = save_research_run(
                'research_web',
                'source pack beta',
                {
                    'ok': True,
                    'sources': [{'source_id': 2, 'title': 'Shared Source', 'final_url': 'https://example.com/shared'}],
                    'claims': [{'claim_id': 1, 'claim': 'Second run claim.', 'supporting_sources': [2]}],
                    'evidence': [],
                },
                root=root / 'runs',
            )

            pack = collect_source_pack([first['run_id'], second['run_id']], runs_root=root / 'runs')

        self.assertTrue(pack['ok'])
        self.assertEqual(pack['counts']['runs'], 2)
        self.assertEqual(pack['counts']['sources'], 1)
        self.assertEqual(pack['sources'][0]['run_ids'], sorted([first['run_id'], second['run_id']]))
        self.assertEqual(pack['counts']['claims'], 2)
        self.assertEqual(pack['counts']['evidence'], 1)

    def test_collect_source_pack_can_redact_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run(
                'research_web',
                'redact pack',
                {
                    'ok': True,
                    'sources': [{'source_id': 1, 'title': 'Private https://private.example/title', 'final_url': 'https://private.example/doc'}],
                    'claims': [{'claim_id': 1, 'claim': 'See https://private.example/doc.', 'supporting_sources': [1]}],
                    'evidence': [{'source_id': 1, 'url': 'https://private.example/doc', 'quote': 'Quote https://private.example/doc'}],
                },
                root=root / 'runs',
            )

            pack = collect_source_pack([saved['run_id']], runs_root=root / 'runs', redact=True)

        self.assertEqual(pack['sources'][0]['url'], '[redacted-url]')
        self.assertNotIn('https://private.example', pack['sources'][0]['title'])
        self.assertIn('[redacted-url]', pack['claims'][0]['claim'])
        self.assertEqual(pack['evidence'][0]['url'], '[redacted-url]')

    def test_write_source_pack_writes_jsonl_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = {
                'ok': True,
                'redacted': False,
                'runs': [{'run_id': 'run-1', 'query': 'topic'}],
                'failures': [],
                'counts': {'runs': 1, 'sources': 1, 'claims': 1, 'evidence': 1, 'failures': 0},
                'sources': [{'source_id': 1, 'title': 'Source', 'url': 'https://example.com'}],
                'claims': [{'claim_id': 1, 'claim': 'Claim.'}],
                'evidence': [{'citation': 'source:1[0:10]'}],
            }

            result = write_source_pack(pack, root / 'pack')
            manifest = json.loads((root / 'pack' / 'manifest.json').read_text(encoding='utf-8'))
            index = (root / 'pack' / 'index.md').read_text(encoding='utf-8')
            sources_exists = (root / 'pack' / 'sources.jsonl').exists()

        self.assertTrue(result['ok'])
        self.assertEqual(manifest['counts']['sources'], 1)
        self.assertTrue(sources_exists)
        self.assertIn('Offline Source Pack', index)
        self.assertIn('source:1 Source', source_pack_markdown(pack))


if __name__ == '__main__':
    unittest.main()
