from __future__ import annotations

import asyncio
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.research_campaign import ROOT
from web_research.campaign_synthesis import apply_campaign_narrative_synthesis, build_campaign_synthesis, write_campaign_synthesis_bundle
from web_research.campaigns import create_research_campaign
from web_research.jobs import finish_research_job, lease_next_research_job
from web_research.runs import save_research_run


def _save_run(root: Path, query: str, claim: str, *, title: str = 'Shared Source') -> dict:
    return save_research_run(
        'deep_research',
        query,
        {
            'ok': True,
            'final_report': f'# {query}\n\n{claim}\n',
            'sources': [
                {
                    'source_id': 1,
                    'title': title,
                    'final_url': 'https://example.com/shared',
                    'reliability': {'source_type': 'documentation', 'reliability_weight': 'strong'},
                    'fetched_at': '2026-06-05T00:00:00Z',
                    'rendered': True,
                }
            ],
            'claims': [
                {
                    'claim_id': 1,
                    'claim': claim,
                    'confidence': 'medium',
                    'supporting_sources': [1],
                    'supporting_evidence': [{'citation': 'source:1[0:10]'}],
                }
            ],
            'research_quality': {'label': 'strong', 'score': 82},
            'citation_audit': {'ok': True},
            'final_answer_review': {'ok': True, 'issue_count': 0},
        },
        root=root,
    )


def _complete_next_job(jobs_root: Path, run_id: str) -> None:
    leased = lease_next_research_job(jobs_root, worker_id='test-worker')
    finish_research_job(
        jobs_root,
        leased['job']['job_id'],
        lease_id=leased['lease_id'],
        status='completed',
        event='completed',
        run_id=run_id,
    )


class CampaignSynthesisTests(unittest.TestCase):
    def test_build_campaign_synthesis_merges_runs_and_dedupes_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(
                root / 'campaigns',
                objective='Compare local research agents',
                queue=True,
                jobs_root=root / 'jobs',
            )
            first = _save_run(root / 'runs', 'landscape', 'Campaigns split a large objective into subjobs.')
            second = _save_run(root / 'runs', 'sources', 'Campaign synthesis merges subjob reports.')
            _complete_next_job(root / 'jobs', first['run_id'])
            _complete_next_job(root / 'jobs', second['run_id'])

            result = build_campaign_synthesis(
                created['campaign']['campaign_id'],
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
            )

        self.assertTrue(result['ok'])
        self.assertEqual(result['run_count'], 2)
        self.assertEqual(result['source_count'], 1)
        self.assertEqual(result['claim_count'], 2)
        self.assertIn('Campaign Dossier', result['dossier'])
        self.assertIn('Campaign synthesis merges subjob reports.', result['dossier'])
        self.assertEqual(result['claims'][0]['supporting_campaign_sources'], '1')

    def test_write_campaign_synthesis_bundle_writes_dossier_indexes_and_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(
                root / 'campaigns',
                objective='Build a final dossier',
                queue=True,
                jobs_root=root / 'jobs',
            )
            saved = _save_run(root / 'runs', 'dossier', 'Dossiers include source and claim tables.')
            _complete_next_job(root / 'jobs', saved['run_id'])

            result = write_campaign_synthesis_bundle(
                created['campaign']['campaign_id'],
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                output_dir=root / 'exports',
            )

            bundle = Path(result['bundle_dir'])
            manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
            sources = list(csv.DictReader((bundle / 'sources.csv').open(encoding='utf-8')))
            claims = list(csv.DictReader((bundle / 'claims.csv').open(encoding='utf-8')))
            index = (bundle / 'index.md').read_text(encoding='utf-8')

        self.assertTrue(result['ok'])
        self.assertIn('dossier.md', result['files'])
        self.assertIn('manifest.json', result['files'])
        self.assertEqual(manifest['counts']['completed_runs'], 1)
        self.assertEqual(manifest['counts']['deduped_sources'], 1)
        self.assertEqual(sources[0]['url'], 'https://example.com/shared')
        self.assertEqual(claims[0]['claim'], 'Dossiers include source and claim tables.')
        self.assertIn('[dossier.md](dossier.md)', index)

    def test_apply_campaign_narrative_synthesis_writes_polished_dossier_metadata(self) -> None:
        async def fake_synthesize_campaign_dossier(synthesis: dict, *, deterministic_dossier: str, enabled: bool | None = None) -> dict:
            return {
                'enabled': True,
                'used': True,
                'model': 'fake-model',
                'message': 'ok',
                'validation': {'ok': True, 'cited_source_ids': [1], 'unknown_source_ids': []},
                'dossier': '# Polished Campaign\n\nUses source:1.\n',
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(
                root / 'campaigns',
                objective='Polish a final dossier',
                queue=True,
                jobs_root=root / 'jobs',
            )
            saved = _save_run(root / 'runs', 'polish', 'Local synthesis can polish campaign dossiers.')
            _complete_next_job(root / 'jobs', saved['run_id'])
            synthesis = build_campaign_synthesis(
                created['campaign']['campaign_id'],
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
            )
            with patch('web_research.campaign_synthesis.synthesize_campaign_dossier', fake_synthesize_campaign_dossier):
                polished = asyncio.run(apply_campaign_narrative_synthesis(synthesis, enabled=True))
            result = write_campaign_synthesis_bundle(
                created['campaign']['campaign_id'],
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                output_dir=root / 'exports',
                synthesis=polished,
            )
            bundle = Path(result['bundle_dir'])
            dossier = (bundle / 'dossier.md').read_text(encoding='utf-8')
            manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
            audit = json.loads((bundle / 'audit.json').read_text(encoding='utf-8'))

        self.assertIn('# Polished Campaign', dossier)
        self.assertTrue(manifest['campaign_synthesis']['used'])
        self.assertEqual(manifest['campaign_synthesis']['model'], 'fake-model')
        self.assertTrue(audit['campaign_synthesis']['validation']['ok'])

    def test_cli_synthesize_previews_by_default_and_writes_with_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(
                root / 'campaigns',
                objective='CLI campaign synthesis',
                queue=True,
                jobs_root=root / 'jobs',
            )
            saved = _save_run(root / 'runs', 'cli', 'The CLI writes campaign synthesis bundles.')
            _complete_next_job(root / 'jobs', saved['run_id'])
            campaign_id = created['campaign']['campaign_id']

            preview = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts' / 'research_campaign.py'),
                    '--root',
                    str(root / 'campaigns'),
                    '--jobs-root',
                    str(root / 'jobs'),
                    '--runs-root',
                    str(root / 'runs'),
                    'synthesize',
                    campaign_id,
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
            applied = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts' / 'research_campaign.py'),
                    '--root',
                    str(root / 'campaigns'),
                    '--jobs-root',
                    str(root / 'jobs'),
                    '--runs-root',
                    str(root / 'runs'),
                    'synthesize',
                    campaign_id,
                    '--output-dir',
                    str(root / 'exports'),
                    '--apply',
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
            applied_payload = json.loads(applied.stdout) if applied.stdout else {}
            bundle_exists = Path(applied_payload.get('bundle_dir', '')).exists()

        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertTrue(json.loads(preview.stdout)['dry_run'])
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertTrue(bundle_exists)


if __name__ == '__main__':
    unittest.main()
