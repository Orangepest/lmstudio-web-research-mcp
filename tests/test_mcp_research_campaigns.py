from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_server.server import safe_research_campaign, safe_synthesize_research_campaign
from web_research.campaigns import create_research_campaign
from web_research.jobs import finish_research_job, lease_next_research_job
from web_research.runs import save_research_run


class MCPResearchCampaignTests(unittest.TestCase):
    def test_safe_research_campaign_previews_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
            ):
                result = safe_research_campaign('Compare local research agents\ndepth=deep\nqueue=true')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertTrue(result['would_queue_jobs'])
        self.assertEqual(result['planned_campaign']['step_count'], 9)
        self.assertFalse((root / 'campaigns').exists())
        self.assertFalse((root / 'jobs').exists())

    def test_safe_research_campaign_apply_queues_campaign_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
            ):
                result = safe_research_campaign('Compare local research agents\ndepth=standard\nqueue=true\napply=true')

        self.assertTrue(result['ok'])
        self.assertFalse(result['dry_run'])
        self.assertEqual(result['campaign']['step_count'], 6)
        self.assertEqual(len(result['queued_jobs']), 6)

    def test_safe_research_campaign_status_lists_campaigns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(root / 'campaigns', objective='Map local research agents')
            with patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'):
                listed = safe_research_campaign('status')
                explicit = safe_research_campaign(f"campaign_id: {created['campaign']['campaign_id']}")

        self.assertTrue(listed['ok'])
        self.assertEqual(listed['count'], 1)
        self.assertTrue(explicit['ok'])
        self.assertEqual(explicit['campaign']['campaign_id'], created['campaign']['campaign_id'])

    def test_safe_research_campaign_preview_rejects_invalid_depth(self) -> None:
        result = safe_research_campaign('Compare local research agents\ndepth=wide')

        self.assertFalse(result['ok'])
        self.assertIn('depth must be', result['message'])

    def test_safe_synthesize_research_campaign_previews_and_applies_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(
                root / 'campaigns',
                objective='Synthesize campaign through MCP',
                queue=True,
                jobs_root=root / 'jobs',
            )
            saved = save_research_run(
                'deep_research',
                'mcp campaign synthesis',
                {
                    'ok': True,
                    'final_report': '# MCP\n\nCampaign synthesis works.',
                    'sources': [
                        {
                            'source_id': 1,
                            'title': 'MCP Docs',
                            'final_url': 'https://example.com/mcp',
                            'reliability': {'source_type': 'documentation', 'reliability_weight': 'strong'},
                        }
                    ],
                    'claims': [{'claim_id': 1, 'claim': 'Campaign synthesis works.', 'supporting_sources': [1]}],
                    'research_quality': {'label': 'strong', 'score': 80},
                },
                root=root / 'runs',
            )
            leased = lease_next_research_job(root / 'jobs', worker_id='mcp-test')
            finish_research_job(
                root / 'jobs',
                leased['job']['job_id'],
                lease_id=leased['lease_id'],
                status='completed',
                event='completed',
                run_id=saved['run_id'],
            )
            campaign_id = created['campaign']['campaign_id']
            with (
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_CAMPAIGN_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                preview = safe_synthesize_research_campaign(f'campaign_id: {campaign_id}')
                applied = safe_synthesize_research_campaign(f'campaign_id: {campaign_id}\napply=true')
                bundle_exists = Path(applied.get('bundle_dir', '')).exists()

        self.assertTrue(preview['ok'])
        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['run_count'], 1)
        self.assertFalse((root / 'syntheses').exists())
        self.assertTrue(applied['ok'])
        self.assertFalse(applied['dry_run'])
        self.assertTrue(bundle_exists)
        self.assertIn('dossier.md', applied['files'])

    def test_safe_synthesize_research_campaign_can_request_local_synthesis_preview(self) -> None:
        async def fake_apply_campaign_narrative_synthesis(synthesis: dict, *, enabled: bool | None = None) -> dict:
            updated = dict(synthesis)
            updated['campaign_synthesis'] = {'enabled': True, 'used': True, 'model': 'fake-model', 'message': 'ok'}
            updated['dossier'] = '# Polished\n\nUses source:1.\n'
            return updated

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(
                root / 'campaigns',
                objective='Preview local campaign synthesis',
                queue=True,
                jobs_root=root / 'jobs',
            )
            saved = save_research_run(
                'deep_research',
                'mcp local campaign synthesis',
                {
                    'ok': True,
                    'final_report': '# MCP\n\nCampaign synthesis works.',
                    'sources': [{'source_id': 1, 'title': 'MCP Docs', 'final_url': 'https://example.com/mcp'}],
                    'claims': [{'claim_id': 1, 'claim': 'Campaign synthesis works.', 'supporting_sources': [1]}],
                },
                root=root / 'runs',
            )
            leased = lease_next_research_job(root / 'jobs', worker_id='mcp-test')
            finish_research_job(
                root / 'jobs',
                leased['job']['job_id'],
                lease_id=leased['lease_id'],
                status='completed',
                event='completed',
                run_id=saved['run_id'],
            )
            campaign_id = created['campaign']['campaign_id']
            with (
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.apply_campaign_narrative_synthesis', fake_apply_campaign_narrative_synthesis),
            ):
                preview = safe_synthesize_research_campaign(f'campaign_id: {campaign_id}\nlocal_synthesis=true')

        self.assertTrue(preview['ok'])
        self.assertTrue(preview['dry_run'])
        self.assertTrue(preview['campaign_synthesis']['used'])
        self.assertEqual(preview['campaign_synthesis']['model'], 'fake-model')


if __name__ == '__main__':
    unittest.main()
