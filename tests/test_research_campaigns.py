from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web_research.campaigns import (
    create_research_campaign,
    list_research_campaigns,
    load_research_campaign,
    normalize_campaign_depth,
    parse_campaign_request,
    plan_campaign_questions,
    summarize_campaign,
)
from web_research.jobs import create_research_job, finish_research_job, lease_next_research_job, list_research_jobs


class ResearchCampaignTests(unittest.TestCase):
    def test_plan_campaign_questions_scales_by_depth(self) -> None:
        standard = plan_campaign_questions('local AI research assistants', depth='standard')
        deep = plan_campaign_questions('local AI research assistants', depth='deep')
        exhaustive = plan_campaign_questions('local AI research assistants', depth='exhaustive')

        self.assertEqual(len(standard), 6)
        self.assertGreater(len(deep), len(standard))
        self.assertGreater(len(exhaustive), len(deep))
        self.assertEqual(standard[0]['step_id'], '01-landscape')
        self.assertIn('local AI research assistants', standard[0]['question'])

    def test_create_campaign_can_queue_tagged_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = create_research_campaign(
                root / 'campaigns',
                objective='Compare local deep research agents',
                profile='fast',
                depth='deep',
                priority=10,
                queue=True,
                jobs_root=root / 'jobs',
            )
            loaded = load_research_campaign(root / 'campaigns', result['campaign']['campaign_id'])
            jobs = list_research_jobs(root / 'jobs', limit=20)

        self.assertTrue(result['ok'])
        self.assertEqual(result['campaign']['status'], 'queued')
        self.assertEqual(result['campaign']['step_count'], 9)
        self.assertEqual(len(result['queued_jobs']), 9)
        self.assertTrue(loaded['ok'])
        self.assertEqual(jobs['count'], 9)
        self.assertTrue(all(f"campaign:{result['campaign']['campaign_id']}" in job['tags'] for job in jobs['jobs']))
        self.assertEqual(min(job['priority'] for job in jobs['jobs']), 10)
        self.assertEqual(max(job['priority'] for job in jobs['jobs']), 18)

    def test_campaign_summary_derives_completed_step_runs_from_tagged_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(
                root / 'campaigns',
                objective='Compare local deep research agents',
                profile='fast',
                depth='standard',
                queue=True,
                jobs_root=root / 'jobs',
            )
            campaign_id = created['campaign']['campaign_id']
            leased = lease_next_research_job(root / 'jobs', worker_id='worker-1')
            job_id = leased['job']['job_id']
            finish_research_job(
                root / 'jobs',
                job_id,
                lease_id=leased['lease_id'],
                status='completed',
                event='completed',
                run_id='run-1',
            )
            loaded = load_research_campaign(root / 'campaigns', campaign_id)
            summary = summarize_campaign(loaded['campaign'], jobs_root=root / 'jobs')

        self.assertEqual(summary['step_status_counts']['completed'], 1)
        self.assertIn('run-1', summary['run_ids'])
        completed = [step for step in summary['steps'] if step['status'] == 'completed']
        self.assertEqual(completed[0]['run_ids'], ['run-1'])

    def test_campaign_summary_scans_all_campaign_jobs_beyond_listing_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(
                root / 'campaigns',
                objective='Compare local deep research agents',
                profile='fast',
                depth='standard',
            )
            campaign = load_research_campaign(root / 'campaigns', created['campaign']['campaign_id'])['campaign']
            step_id = campaign['steps'][0]['step_id']
            for index in range(105):
                create_research_job(
                    root / 'jobs',
                    request=f'large campaign job {index}',
                    profile='fast',
                    priority=index,
                    status='completed',
                    tags=[f"campaign:{campaign['campaign_id']}", f'campaign_step:{step_id}'],
                )
            summary = summarize_campaign(campaign, jobs_root=root / 'jobs')

        self.assertEqual(len(summary['jobs']), 105)
        self.assertEqual(summary['steps'][0]['status'], 'completed')

    def test_create_campaign_with_queue_missing_jobs_root_does_not_create_orphan_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = create_research_campaign(
                root / 'campaigns',
                objective='Compare local deep research agents',
                queue=True,
                jobs_root=None,
            )

            self.assertFalse((root / 'campaigns').exists())

        self.assertFalse(result['ok'])
        self.assertIn('jobs_root', result['message'])

    def test_invalid_campaign_depth_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_campaign_depth('wide')

        with tempfile.TemporaryDirectory() as tmp:
            result = create_research_campaign(Path(tmp), objective='Compare local agents', depth='wide')

        self.assertFalse(result['ok'])
        self.assertIn('depth must be', result['message'])

    def test_list_campaigns_returns_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_campaign(
                root / 'campaigns',
                objective='Map local research systems',
                profile='careful',
                depth='standard',
            )
            listed = list_research_campaigns(root / 'campaigns')

        self.assertTrue(created['ok'])
        self.assertEqual(listed['count'], 1)
        self.assertEqual(listed['campaigns'][0]['objective'], 'Map local research systems')

    def test_parse_campaign_request_extracts_options(self) -> None:
        parsed = parse_campaign_request(
            """
            objective: Compare local research systems
            profile=exhaustive
            depth=deep
            queue=true
            apply=true
            """
        )

        self.assertEqual(parsed['objective'], 'Compare local research systems')
        self.assertEqual(parsed['options']['profile'], 'exhaustive')
        self.assertEqual(parsed['options']['depth'], 'deep')
        self.assertEqual(parsed['options']['queue'], 'true')


if __name__ == '__main__':
    unittest.main()
