from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web_research.jobs import list_research_jobs
from web_research.mission_runtime import parse_runtime_request, research_mission_runtime


class ResearchMissionRuntimeTests(unittest.TestCase):
    def test_parse_runtime_request_accepts_actions_and_question(self) -> None:
        parsed = parse_runtime_request(
            """
            question: Compare local deep research tools
            profile: careful
            submit=true
            start_worker=true
            """
        )

        self.assertEqual(parsed['request'], 'Compare local deep research tools')
        self.assertEqual(parsed['options']['profile'], 'careful')
        self.assertEqual(parsed['options']['submit'], 'true')
        self.assertEqual(parsed['options']['start_worker'], 'true')

    def test_runtime_status_is_preview_safe_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = research_mission_runtime(
                'status',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                worker_state_dir=root / 'worker',
            )

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['actions'], ['status'])
        self.assertEqual(result['status']['job_counts']['queued'], 0)
        self.assertFalse(result['status']['worker']['running'])

    def test_runtime_submit_requires_apply_to_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preview = research_mission_runtime(
                'Compare local research agents\nsubmit=true',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                worker_state_dir=root / 'worker',
            )
            submitted = research_mission_runtime(
                'Compare local research agents\nsubmit=true\napply=true\nprofile=fast\npriority=3',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                worker_state_dir=root / 'worker',
            )
            jobs = list_research_jobs(root / 'jobs')

        self.assertTrue(preview['dry_run'])
        self.assertIn('planned_job', preview)
        self.assertFalse((root / 'jobs').exists())
        self.assertFalse(submitted['dry_run'])
        self.assertTrue(submitted['submitted_job']['ok'])
        self.assertEqual(jobs['count'], 1)
        self.assertEqual(jobs['jobs'][0]['profile'], 'fast')
        self.assertEqual(jobs['jobs'][0]['priority'], 3)

    def test_runtime_start_worker_dry_run_uses_control_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('web_research.mission_runtime.start_worker') as start:
                start.return_value = {'ok': True, 'started': False, 'dry_run': True, 'state': {'command': ['worker']}}
                result = research_mission_runtime(
                    'start_worker=true',
                    jobs_root=root / 'jobs',
                    runs_root=root / 'runs',
                    worker_state_dir=root / 'worker',
                )

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertIn('preview_start_worker', result['actions'])
        start.assert_called_once()
        self.assertTrue(start.call_args.kwargs['dry_run'])


if __name__ == '__main__':
    unittest.main()
