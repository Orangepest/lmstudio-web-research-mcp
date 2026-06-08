from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_server.server import safe_research_director
from web_research.jobs import create_research_job, load_research_job


class MCPResearchDirectorTests(unittest.TestCase):
    def test_safe_research_director_previews_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                result = safe_research_director('objective: Build a local research director\ndepth=standard')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['planned_director']['initial_step_count'], 6)
        self.assertFalse((root / 'directors').exists())
        self.assertFalse((root / 'campaigns').exists())

    def test_safe_research_director_apply_creates_director(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                result = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                status = safe_research_director('status')

        self.assertTrue(result['ok'])
        self.assertFalse(result['dry_run'])
        self.assertEqual(result['director']['initial_job_count'], 6)
        self.assertEqual(status['count'], 1)

    def test_safe_research_director_wave_preview_uses_director_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                wave = safe_research_director(f'director_id: {director_id}\naction: wave\nstart_worker=true')

        self.assertTrue(wave['ok'])
        self.assertTrue(wave['dry_run'])
        self.assertEqual(wave['stop_reason'], 'waiting_for_worker')
        self.assertTrue(wave['worker_start']['dry_run'])

    def test_safe_research_director_dashboard_preview_returns_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                dashboard = safe_research_director(f'director_id: {director_id}\naction: dashboard')

        self.assertTrue(dashboard['ok'])
        self.assertTrue(dashboard['dry_run'])
        self.assertIn('Research Director', dashboard['markdown'])
        self.assertIn('quality_gate', dashboard['assessment'])

    def test_safe_research_director_recovery_can_cancel_stuck_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                stuck = create_research_job(root / 'jobs', request='stuck', tags=[f'director:{director_id}'])
                path = Path(stuck['job_path'])
                payload = json.loads(path.read_text(encoding='utf-8'))
                payload['updated_at'] = '2026-01-01T00:00:00Z'
                path.write_text(json.dumps(payload), encoding='utf-8')
                preview = safe_research_director(f'director_id: {director_id}\naction: recovery\nstale_hours=1')
                applied = safe_research_director(
                    f'director_id: {director_id}\naction: recovery\nstale_hours=1\ncancel_stuck_jobs=true\napply=true'
                )
                loaded = load_research_job(root / 'jobs', stuck['job']['job_id'])

        self.assertEqual(preview['issue_counts']['stuck_jobs'], 1)
        self.assertEqual(applied['issue_counts']['cancelled_jobs'], 1)
        self.assertEqual(loaded['job']['status'], 'cancelled')

    def test_safe_research_director_recovery_policy_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                result = safe_research_director(f'director_id: {director_id}\naction: recovery\npolicy=balanced')

        self.assertTrue(result['dry_run'])
        self.assertEqual(result['policy']['name'], 'balanced')
        self.assertTrue(result['worker_recovery']['allowed'])
        self.assertTrue(result['checkpoint_recovery']['allowed'])

    def test_safe_research_director_graph_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                graph = safe_research_director(f'director_id: {director_id}\naction: graph')

        self.assertTrue(graph['ok'])
        self.assertTrue(graph['dry_run'])
        self.assertIn('nodes', graph)
        self.assertIn('edges', graph)
        self.assertGreaterEqual(graph['counts']['nodes'], 2)

    def test_safe_research_director_graph_actions_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                result = safe_research_director(f'director_id: {director_id}\naction: graph_actions')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertIn('planned_jobs', result)
        self.assertIn('graph_summary', result)

    def test_safe_research_director_runbook_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                result = safe_research_director(f'director_id: {director_id}\naction: runbook')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertIn('commands', result)
        self.assertIn('markdown', result)

    def test_safe_research_director_runbook_export_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                result = safe_research_director(f'director_id: {director_id}\naction: runbook_export\nprofile=private-share')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['profile']['name'], 'private-share')
        self.assertTrue(result['profile']['redact'])

    def test_safe_research_director_compare_bundles_requires_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                result = safe_research_director(f'director_id: {director_id}\naction: compare_bundles')

        self.assertFalse(result['ok'])
        self.assertIn('left=<path>', result['message'])

    def test_safe_research_director_comparison_actions_requires_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                result = safe_research_director(f'director_id: {director_id}\naction: comparison_actions')

        self.assertFalse(result['ok'])
        self.assertIn('left=<path>', result['message'])

    def test_safe_research_director_comparison_replay_requires_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch('mcp_server.server.MCP_RESEARCH_DIRECTOR_ROOT', root / 'directors'),
                patch('mcp_server.server.MCP_RESEARCH_CAMPAIGN_ROOT', root / 'campaigns'),
                patch('mcp_server.server.MCP_RESEARCH_JOBS_ROOT', root / 'jobs'),
                patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root / 'runs'),
                patch('mcp_server.server.MCP_DIRECTOR_SYNTHESIS_ROOT', root / 'syntheses'),
            ):
                created = safe_research_director('objective: Build a local research director\ndepth=standard\napply=true')
                director_id = created['director']['director_id']
                result = safe_research_director(f'director_id: {director_id}\naction: comparison_replay')

        self.assertFalse(result['ok'])
        self.assertIn('No comparison-action event', result['message'])


if __name__ == '__main__':
    unittest.main()
