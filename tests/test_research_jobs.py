from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from web_research.jobs import (
    create_research_job,
    finish_research_job,
    heartbeat_research_job,
    lease_next_research_job,
    list_research_jobs,
    load_research_job,
    mark_research_job_running,
    update_research_job,
)


class ResearchJobsTests(unittest.TestCase):
    def test_create_load_and_list_research_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_job(root, request='Compare local research tools', profile='careful', priority=3, tags=['local'])
            loaded = load_research_job(root, created['job']['job_id'])
            listed = list_research_jobs(root)

        self.assertTrue(created['ok'])
        self.assertTrue(loaded['ok'])
        self.assertEqual(loaded['job']['request'], 'Compare local research tools')
        self.assertEqual(listed['count'], 1)
        self.assertEqual(listed['jobs'][0]['profile'], 'careful')
        self.assertEqual(listed['jobs'][0]['tags'], ['local'])

    def test_create_research_job_requires_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = create_research_job(Path(tmp), request='')

        self.assertFalse(result['ok'])
        self.assertIn('required', result['message'])

    def test_update_research_job_records_status_run_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_job(root, request='Compare local research tools')
            job_id = created['job']['job_id']

            updated = update_research_job(root, job_id, status='running', event='started', run_id='run-1', message='Started.')
            loaded = load_research_job(root, job_id)

        self.assertTrue(updated['ok'])
        self.assertEqual(updated['job']['status'], 'running')
        self.assertEqual(updated['job']['run_ids'], ['run-1'])
        self.assertEqual(loaded['job']['events'][-1]['event'], 'started')
        self.assertEqual(loaded['job']['events'][-1]['message'], 'Started.')

    def test_list_research_jobs_filters_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queued = create_research_job(root, request='Queued job')
            done = create_research_job(root, request='Done job')
            update_research_job(root, done['job']['job_id'], status='completed')

            listed = list_research_jobs(root, status='queued')

        self.assertEqual(listed['count'], 1)
        self.assertEqual(listed['jobs'][0]['job_id'], queued['job']['job_id'])

    def test_lease_next_research_job_claims_highest_priority_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            low = create_research_job(root, request='Low priority', priority=1)
            high = create_research_job(root, request='High priority', priority=5)

            leased = lease_next_research_job(root, worker_id='worker-1', lease_seconds=60)
            next_lease = lease_next_research_job(root, worker_id='worker-2', lease_seconds=60)

        self.assertTrue(leased['ok'])
        self.assertTrue(leased['leased'])
        self.assertEqual(leased['job']['job_id'], high['job']['job_id'])
        self.assertEqual(leased['job']['status'], 'leased')
        self.assertEqual(leased['job']['attempt_count'], 1)
        self.assertEqual(next_lease['job']['job_id'], low['job']['job_id'])

    def test_lease_lifecycle_heartbeat_running_and_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_job(root, request='Queued job')
            job_id = created['job']['job_id']
            leased = lease_next_research_job(root, worker_id='worker-1', lease_seconds=60)
            lease_id = leased['lease_id']

            heartbeat = heartbeat_research_job(root, job_id, lease_id=lease_id, lease_seconds=120)
            running = mark_research_job_running(root, job_id, lease_id=lease_id)
            finished = finish_research_job(root, job_id, lease_id=lease_id, status='completed', event='completed', run_id='run-1')
            loaded = load_research_job(root, job_id)

        self.assertTrue(heartbeat['ok'])
        self.assertTrue(running['ok'])
        self.assertTrue(finished['ok'])
        self.assertEqual(loaded['job']['status'], 'completed')
        self.assertEqual(loaded['job']['lease_id'], None)
        self.assertEqual(loaded['job']['run_ids'], ['run-1'])

    def test_lease_finish_rejects_wrong_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_job(root, request='Queued job')
            result = finish_research_job(
                root,
                created['job']['job_id'],
                lease_id='wrong',
                status='completed',
                event='completed',
            )

        self.assertFalse(result['ok'])
        self.assertIn('lease does not match', result['message'])

    def test_cli_add_and_list_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = subprocess.run(
                [sys.executable, 'scripts/research_jobs.py', '--root', str(root), '--json', 'add', 'Compare local research tools'],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )
            listed = subprocess.run(
                [sys.executable, 'scripts/research_jobs.py', '--root', str(root), '--json', 'list'],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

        self.assertTrue(json.loads(add.stdout)['ok'])
        self.assertEqual(json.loads(listed.stdout)['count'], 1)


if __name__ == '__main__':
    unittest.main()
