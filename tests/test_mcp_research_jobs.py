from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_server.server import (
    _parse_safe_research_job_request,
    safe_cancel_research_job,
    safe_research_job_status,
    safe_research_runtime,
    safe_submit_research_job,
)
from web_research.jobs import create_research_job, load_research_job


class MCPResearchJobsTests(unittest.TestCase):
    def test_parse_safe_research_job_request_accepts_options(self) -> None:
        parsed = _parse_safe_research_job_request(
            """
            question: Compare local research agents
            profile: careful
            priority: 4
            tag: local, agents
            submit: true
            """
        )

        self.assertEqual(parsed['request'], 'Compare local research agents')
        self.assertEqual(parsed['options']['profile'], 'careful')
        self.assertEqual(parsed['options']['priority'], '4')
        self.assertEqual(parsed['tags'], ['local', 'agents'])

    def test_safe_submit_research_job_previews_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root):
                result = safe_submit_research_job('Compare local research agents\nprofile=careful')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['planned_job']['profile'], 'careful')
        self.assertFalse(any(root.glob('*/job.json')))

    def test_safe_submit_research_job_apply_writes_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root):
                result = safe_submit_research_job('Compare local research agents\nprofile=fast\npriority=2\nsubmit=true')
                job_path_exists = Path(result['job_path']).exists()

        self.assertTrue(result['ok'])
        self.assertFalse(result['dry_run'])
        self.assertEqual(result['job']['status'], 'queued')
        self.assertEqual(result['job']['profile'], 'fast')
        self.assertEqual(result['job']['priority'], 2)
        self.assertTrue(job_path_exists)

    def test_safe_research_job_status_lists_and_filters_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_job(root, request='Queued research', profile='careful')
            with patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root):
                listed = safe_research_job_status('queued\nlimit=3')
                explicit = safe_research_job_status(f"job_id: {created['job']['job_id']}")

        self.assertTrue(listed['ok'])
        self.assertEqual(listed['selector'], 'queued')
        self.assertEqual(listed['count'], 1)
        self.assertEqual(explicit['job_count'], 1)
        self.assertEqual(explicit['jobs'][0]['request'], 'Queued research')

    def test_safe_research_job_status_rejects_bad_limit(self) -> None:
        result = safe_research_job_status('limit=nope')

        self.assertFalse(result['ok'])
        self.assertIn('limit must be an integer', result['message'])

    def test_safe_cancel_research_job_requires_explicit_id(self) -> None:
        result = safe_cancel_research_job('queued')

        self.assertFalse(result['ok'])
        self.assertIn('job_id', result['message'])

    def test_safe_cancel_research_job_updates_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_job(root, request='Queued research')
            job_id = created['job']['job_id']
            with patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root):
                result = safe_cancel_research_job(f'job_id: {job_id}')
            loaded = load_research_job(root, job_id)

        self.assertTrue(result['ok'])
        self.assertEqual(result['cancelled_count'], 1)
        self.assertEqual(loaded['job']['status'], 'cancelled')
        self.assertEqual(loaded['job']['events'][-1]['event'], 'cancelled')

    def test_safe_research_runtime_delegates_to_runtime_layer(self) -> None:
        with patch('mcp_server.server.research_mission_runtime', return_value={'ok': True, 'tool': 'safe_research_runtime'}) as runtime:
            result = safe_research_runtime('status')

        self.assertTrue(result['ok'])
        runtime.assert_called_once()
        self.assertEqual(runtime.call_args.args[0], 'status')
        self.assertIn('jobs_root', runtime.call_args.kwargs)
        self.assertIn('worker_state_dir', runtime.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
