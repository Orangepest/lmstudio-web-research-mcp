from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.export_research_run import ROOT, export_research_run, export_research_runs, select_research_run_ids
from web_research.runs import save_research_run


class ExportResearchRunTests(unittest.TestCase):
    def test_export_research_run_writes_bundle_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run(
                'deep_research',
                'export topic',
                {
                    'ok': True,
                    'final_report': '# Report\n\nUses source:1.\n',
                    'sources': [
                        {
                            'source_id': 1,
                            'title': 'Docs',
                            'final_url': 'https://example.com/docs',
                            'reliability': {'source_type': 'documentation', 'reliability_weight': 'strong'},
                            'fetched_at': '2026-06-05T00:00:00Z',
                            'rendered': True,
                        }
                    ],
                    'searches': [{'intent': 'initial'}, {'intent': 'gap_follow_up'}],
                    'claims': [
                        {
                            'claim_id': 1,
                            'claim': 'Export bundles include claims.',
                            'confidence': 'low',
                            'supporting_sources': [1],
                            'supporting_evidence': [{'citation': 'source:1[0:10]'}],
                        }
                    ],
                    'research_quality': {'label': 'moderate', 'score': 60},
                    'source_quality': {
                        'label': 'limited',
                        'score': 45,
                        'downgrade_reasons': [{'reason': 'low_domain_diversity', 'message': 'Only one domain.'}],
                    },
                    'research_coverage': {
                        'planned_intent_count': 2,
                        'satisfied_intent_count': 1,
                        'missing_intents': ['primary_source'],
                    },
                    'source_freshness': {
                        'current_sensitive': True,
                        'content_freshness_evidence': False,
                        'gaps': ['No recent-change evidence snippets were extracted.'],
                    },
                    'citation_audit': {'ok': True},
                    'final_answer_review': {'ok': False, 'issue_count': 1},
                },
                root=root / 'runs',
            )

            result = export_research_run(saved['run_id'], output_dir=root / 'exports', runs_root=root / 'runs')

            bundle = Path(result['bundle_dir'])
            sources = list(csv.DictReader((bundle / 'sources.csv').open(encoding='utf-8')))
            claims = list(csv.DictReader((bundle / 'claims.csv').open(encoding='utf-8')))
            audit = json.loads((bundle / 'audit.json').read_text(encoding='utf-8'))
            manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
            index = (bundle / 'index.md').read_text(encoding='utf-8')

            self.assertTrue(result['ok'])
            self.assertIn('report.md', result['files'])
            self.assertIn('manifest.json', result['files'])
            self.assertIn('index.md', result['files'])
            self.assertEqual((bundle / 'report.md').read_text(encoding='utf-8'), '# Report\n\nUses source:1.\n')
            self.assertEqual(sources[0]['source_type'], 'documentation')
            self.assertEqual(claims[0]['claim'], 'Export bundles include claims.')
            self.assertEqual(audit['citation_audit'], {'ok': True})
            self.assertEqual(audit['final_answer_review'], {'ok': False, 'issue_count': 1})
            self.assertEqual(manifest['run']['run_id'], saved['run_id'])
            self.assertEqual(manifest['counts']['sources'], 1)
            self.assertEqual(manifest['counts']['claims'], 1)
            self.assertEqual(manifest['budget']['source_count'], 1)
            self.assertEqual(manifest['budget']['rendered_source_count'], 1)
            self.assertEqual(manifest['budget']['follow_up_search_count'], 1)
            self.assertEqual(manifest['quality']['research_label'], 'moderate')
            self.assertFalse(manifest['quality']['final_answer_review_ok'])
            self.assertEqual(manifest['quality']['final_answer_review_issue_count'], 1)
            self.assertEqual(manifest['quality']['source_downgrade_reasons'][0]['reason'], 'low_domain_diversity')
            self.assertEqual(manifest['quality']['coverage_missing_intents'], ['primary_source'])
            self.assertEqual(manifest['quality']['freshness_gaps'], ['No recent-change evidence snippets were extracted.'])
            self.assertEqual(manifest['files']['index'], 'index.md')
            self.assertIn(f"# Research Export: {saved['run_id']}", index)
            self.assertIn('[report.md](report.md)', index)
            self.assertIn('[manifest.json](manifest.json)', index)
            self.assertIn('Final answer review issues: 1', index)
            self.assertIn('Rendered sources: 1', index)

    def test_export_research_run_can_write_zip_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('research_web', 'zip topic', {'ok': True, 'final_report': '# Zip\n'}, root=root / 'runs')

            result = export_research_run(saved['run_id'], output_dir=root / 'exports', runs_root=root / 'runs', zip_bundle=True)

            self.assertTrue(result['ok'])
            self.assertTrue(Path(result['archive_path']).exists())

    def test_export_research_run_can_redact_share_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run(
                'research_web',
                'private export topic',
                {
                    'ok': True,
                    'final_report': '# Report\n\nRead https://private.example/doc for the answer.\n',
                    'sources': [
                        {
                            'source_id': 1,
                            'title': 'Private https://private.example/title',
                            'final_url': 'https://private.example/doc',
                            'text': 'Secret source text.',
                            'summary': 'Secret summary.',
                            'links': [{'url': 'https://private.example/linked'}],
                            'reliability': {'source_type': 'web', 'reliability_weight': 'supporting'},
                        }
                    ],
                    'claims': [
                        {
                            'claim_id': 1,
                            'claim': 'Private source says see https://private.example/doc.',
                            'confidence': 'low',
                            'supporting_sources': [1],
                            'supporting_evidence': [{'quote': 'Secret quote from https://private.example/doc'}],
                        }
                    ],
                    'blocked_sources': [{'url': 'https://private.example/blocked', 'manual_handoff': {'url': 'https://private.example/blocked'}}],
                    'manual_visit_links': [{'url': 'https://private.example/blocked'}],
                    'research_quality': {'label': 'thin', 'score': 30},
                },
                root=root / 'runs',
            )

            result = export_research_run(saved['run_id'], output_dir=root / 'exports', runs_root=root / 'runs', redact=True)

            bundle = Path(result['bundle_dir'])
            report = (bundle / 'report.md').read_text(encoding='utf-8')
            run_payload = json.loads((bundle / 'run.json').read_text(encoding='utf-8'))
            sources = list(csv.DictReader((bundle / 'sources.csv').open(encoding='utf-8')))
            claims = list(csv.DictReader((bundle / 'claims.csv').open(encoding='utf-8')))
            audit = json.loads((bundle / 'audit.json').read_text(encoding='utf-8'))
            manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
            index = (bundle / 'index.md').read_text(encoding='utf-8')

            self.assertTrue(result['redacted'])
            self.assertNotIn('https://private.example', report)
            self.assertIn('[redacted-url]', report)
            self.assertEqual(run_payload['payload']['sources'], '[redacted]')
            self.assertEqual(run_payload['payload']['final_report'], '[redacted]')
            self.assertEqual(sources[0]['url'], '[redacted-url]')
            self.assertNotIn('https://private.example', sources[0]['title'])
            self.assertIn('[redacted-url]', claims[0]['claim'])
            self.assertEqual(audit['blocked_sources'][0]['url'], '[redacted-url]')
            self.assertEqual(audit['blocked_sources'][0]['manual_handoff']['url'], '[redacted-url]')
            self.assertEqual(audit['manual_visit_links'], '[redacted]')
            self.assertTrue(manifest['redacted'])
            self.assertIn('Redacted export: yes', index)

    def test_cli_private_share_profile_redacts_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run(
                'research_web',
                'cli private export topic',
                {
                    'ok': True,
                    'final_report': '# Report\n\nSee https://private.example/doc.\n',
                    'sources': [{'source_id': 1, 'final_url': 'https://private.example/doc'}],
                },
                root=root / 'runs',
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts' / 'export_research_run.py'),
                    saved['run_id'],
                    '--runs-root',
                    str(root / 'runs'),
                    '--output-dir',
                    str(root / 'exports'),
                    '--profile',
                    'private-share',
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            manifest = json.loads((Path(result['bundle_dir']) / 'manifest.json').read_text(encoding='utf-8'))
            report = (Path(result['bundle_dir']) / 'report.md').read_text(encoding='utf-8')
            self.assertTrue(result['redacted'])
            self.assertTrue(manifest['redacted'])
            self.assertNotIn('https://private.example', report)
            self.assertIn('[redacted-url]', report)

    def test_export_research_runs_writes_batch_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = save_research_run('research_web', 'alpha export topic', {'ok': True, 'final_report': '# Alpha\n'}, root=root / 'runs')
            second = save_research_run('research_web', 'beta export topic', {'ok': True, 'final_report': '# Beta\n'}, root=root / 'runs')

            result = export_research_runs(
                [first['run_id'], second['run_id'], first['run_id']],
                output_dir=root / 'exports',
                runs_root=root / 'runs',
                selector='test',
            )

            batch_manifest = json.loads((root / 'exports' / 'batch_manifest.json').read_text(encoding='utf-8'))
            batch_index = (root / 'exports' / 'index.md').read_text(encoding='utf-8')

            self.assertTrue(result['ok'])
            self.assertEqual(result['requested_count'], 2)
            self.assertEqual(result['exported_count'], 2)
            self.assertEqual(batch_manifest['selector'], 'test')
            self.assertTrue((root / 'exports' / first['run_id'] / 'index.md').exists())
            self.assertTrue((root / 'exports' / second['run_id'] / 'manifest.json').exists())
            self.assertIn(f"[{first['run_id']}]({first['run_id']}/index.md)", batch_index)
            self.assertIn(f"[{second['run_id']}]({second['run_id']}/index.md)", batch_index)

    def test_export_research_runs_reports_empty_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = export_research_runs([], output_dir=root / 'exports', runs_root=root / 'runs', selector='find:none', redact=True)
            batch_manifest = json.loads((root / 'exports' / 'batch_manifest.json').read_text(encoding='utf-8'))

            self.assertFalse(result['ok'])
            self.assertTrue(result['empty'])
            self.assertTrue(result['redacted'])
            self.assertTrue(batch_manifest['redacted'])
            self.assertEqual(result['requested_count'], 0)
            self.assertEqual(result['exported_count'], 0)
            self.assertIn('No research runs matched', result['message'])
            self.assertFalse(batch_manifest['ok'])

    def test_select_research_run_ids_can_find_matching_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run(
                'research_web',
                'quartz battery supply chain',
                {'ok': True, 'final_report': '# Quartz\n'},
                root=root / 'runs',
            )
            save_research_run('research_web', 'unrelated gardening topic', {'ok': True}, root=root / 'runs')

            result = select_research_run_ids(find='quartz battery', limit=5, runs_root=root / 'runs')

            self.assertTrue(result['ok'])
            self.assertEqual(result['selector'], 'find:quartz battery')
            self.assertIn(saved['run_id'], result['run_ids'])


if __name__ == '__main__':
    unittest.main()
