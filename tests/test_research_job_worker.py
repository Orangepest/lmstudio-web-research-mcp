from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from scripts.research_job_worker import run_worker_once, run_worker_watch
from web_research.jobs import create_research_job, load_research_job


class ResearchJobWorkerTests(unittest.TestCase):
    def test_run_worker_once_completes_job_with_run_id(self) -> None:
        async def fake_runner(job: dict) -> dict:
            return {
                'ok': True,
                'run_id': 'run-1',
                'run_path': '/tmp/run.json',
                'sources': [{'source_id': 1}],
                'evidence': [{'source_id': 1}],
                'claims': [{'text': 'claim'}],
                'failures': [],
                'blocked_sources': [],
                'research_quality': {'score': 8, 'label': 'strong'},
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_job(root, request='Queued research')
            result = asyncio.run(run_worker_once(root, worker_id='worker-1', lease_seconds=60, runner=fake_runner))
            loaded = load_research_job(root, created['job']['job_id'])

        self.assertTrue(result['ok'])
        self.assertTrue(result['worked'])
        self.assertEqual(result['run_id'], 'run-1')
        self.assertEqual(loaded['job']['status'], 'completed')
        self.assertEqual(loaded['job']['run_ids'], ['run-1'])
        self.assertEqual(loaded['job']['result']['source_count'], 1)

    def test_run_worker_once_marks_runner_exception_failed(self) -> None:
        async def failing_runner(job: dict) -> dict:
            raise RuntimeError('boom')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_research_job(root, request='Queued research')
            result = asyncio.run(run_worker_once(root, worker_id='worker-1', lease_seconds=60, runner=failing_runner))
            loaded = load_research_job(root, created['job']['job_id'])

        self.assertFalse(result['ok'])
        self.assertTrue(result['worked'])
        self.assertEqual(loaded['job']['status'], 'failed')
        self.assertEqual(loaded['job']['result']['error'], 'boom')

    def test_run_worker_once_noops_when_queue_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(run_worker_once(Path(tmp), worker_id='worker-1', lease_seconds=60))

        self.assertTrue(result['ok'])
        self.assertFalse(result['worked'])
        self.assertFalse(result['leased'])

    def test_run_worker_watch_exits_after_idle_timeout(self) -> None:
        sleeps = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        times = iter([0.0, 0.0, 2.0])

        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(
                run_worker_watch(
                    Path(tmp),
                    worker_id='worker-1',
                    lease_seconds=60,
                    poll_seconds=1,
                    idle_exit_seconds=2,
                    monotonic_fn=lambda: next(times),
                    sleep_fn=fake_sleep,
                )
            )

        self.assertTrue(result['ok'])
        self.assertEqual(result['worked_count'], 0)
        self.assertEqual(result['stop_reason'], 'idle_exit')
        self.assertEqual(sleeps, [1])


if __name__ == '__main__':
    unittest.main()
