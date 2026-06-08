from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.research_job_worker_control import (
    build_tmux_command,
    build_worker_command,
    start_worker,
    status_worker,
)


class ResearchJobWorkerControlTests(unittest.TestCase):
    def test_build_worker_command_includes_watch_flags(self) -> None:
        command = build_worker_command(
            jobs_root=Path('/tmp/jobs'),
            worker_id='worker-1',
            lease_seconds=60,
            max_jobs=0,
            poll_seconds=5,
            idle_exit_seconds=120,
            watch=True,
        )

        self.assertIn('scripts/research_job_worker.py', command[1])
        self.assertIn('--watch', command)
        self.assertIn('--poll-seconds', command)
        self.assertIn('5', command)
        self.assertIn('--idle-exit-seconds', command)
        self.assertIn('120', command)

    def test_build_tmux_command_redirects_to_log(self) -> None:
        command = build_tmux_command(session='research', command=['python', 'worker.py'], log_path=Path('/tmp/worker.log'))

        self.assertEqual(command[:5], ['tmux', 'new-session', '-d', '-s', 'research'])
        self.assertIn('worker.py', command[-1])
        self.assertIn('/tmp/worker.log', command[-1])

    def test_status_worker_reports_dead_pid_from_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / 'worker.json').write_text(json.dumps({'pid': 999999, 'log_path': '/tmp/log'}), encoding='utf-8')
            result = status_worker(state_dir=state_dir)

        self.assertTrue(result['ok'])
        self.assertFalse(result['running'])
        self.assertEqual(result['log_path'], '/tmp/log')

    def test_start_worker_dry_run_returns_state_without_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = start_worker(
                jobs_root=Path(tmp) / 'jobs',
                state_dir=Path(tmp) / 'state',
                worker_id='worker-1',
                lease_seconds=60,
                max_jobs=0,
                poll_seconds=5,
                idle_exit_seconds=0,
                watch=True,
                tmux=True,
                session='research',
                dry_run=True,
            )

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertFalse(result['started'])
        self.assertIn('tmux_command', result['state'])

    def test_start_worker_uses_existing_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / 'state'
            state_dir.mkdir()
            (state_dir / 'worker.json').write_text(json.dumps({'pid': 123, 'log_path': '/tmp/log'}), encoding='utf-8')
            with patch('scripts.research_job_worker_control.pid_alive', return_value=True):
                result = start_worker(
                    jobs_root=Path(tmp) / 'jobs',
                    state_dir=state_dir,
                    worker_id='worker-1',
                    lease_seconds=60,
                    max_jobs=0,
                    poll_seconds=5,
                    idle_exit_seconds=0,
                    watch=True,
                    tmux=False,
                    session='research',
                )

        self.assertTrue(result['ok'])
        self.assertTrue(result['already_running'])
        self.assertFalse(result['started'])


if __name__ == '__main__':
    unittest.main()
