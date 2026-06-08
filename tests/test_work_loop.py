from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.work_loop import make_work_loop_dir, run_work_loop, work_loop_markdown


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class WorkLoopTests(unittest.TestCase):
    def test_make_work_loop_dir_creates_unique_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = make_work_loop_dir(root)
            second = make_work_loop_dir(root)

        self.assertNotEqual(first, second)
        self.assertTrue(first.name)
        self.assertTrue(second.name)

    def test_run_work_loop_runs_until_max_cycles_with_interval(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_loop.build_work_session',
                return_value={'ok': True, 'output_dir': str(root), 'step_count': 2, 'failed_step_count': 0},
            ) as build_session:
                loop = run_work_loop(
                    profile='careful',
                    output_dir=root,
                    duration_minutes=60,
                    interval_minutes=10,
                    max_cycles=3,
                    dry_run=True,
                    monotonic_fn=clock.monotonic,
                    sleep_fn=clock.sleep,
                )

            self.assertTrue(loop['ok'])
            self.assertEqual(loop['stop_reason'], 'max_cycles')
            self.assertEqual(loop['cycle_count'], 3)
            self.assertEqual(clock.sleeps, [600, 600])
            self.assertEqual(build_session.call_count, 3)
            self.assertTrue((root / 'work_loop.json').exists())
            self.assertTrue((root / 'work_loop.md').exists())
            self.assertTrue((root / 'events.jsonl').exists())

    def test_run_work_loop_writes_initial_in_progress_summary_before_first_cycle(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def build_session(**_kwargs):
                summary_path = root / 'work_loop.json'
                self.assertTrue(summary_path.exists())
                summary = json.loads(summary_path.read_text(encoding='utf-8'))
                self.assertTrue(summary['in_progress'])
                self.assertEqual(summary['cycle_count'], 0)
                self.assertIn('pid', summary)
                return {'ok': True, 'output_dir': str(root), 'step_count': 1, 'failed_step_count': 0}

            with patch('scripts.work_loop.build_work_session', side_effect=build_session):
                loop = run_work_loop(
                    profile='fast',
                    output_dir=root,
                    duration_minutes=60,
                    interval_minutes=1,
                    max_cycles=1,
                    monotonic_fn=clock.monotonic,
                    sleep_fn=clock.sleep,
                )

        self.assertTrue(loop['ok'])

    def test_run_work_loop_stops_after_duration_elapsed(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_loop.build_work_session',
                return_value={'ok': True, 'output_dir': str(root), 'step_count': 1, 'failed_step_count': 0},
            ) as build_session:
                loop = run_work_loop(
                    profile='careful',
                    output_dir=root,
                    duration_minutes=0.25,
                    interval_minutes=0.25,
                    monotonic_fn=clock.monotonic,
                    sleep_fn=clock.sleep,
                )

        self.assertTrue(loop['ok'])
        self.assertEqual(loop['stop_reason'], 'duration_elapsed')
        self.assertEqual(loop['cycle_count'], 1)
        self.assertEqual(clock.sleeps, [15])
        self.assertEqual(build_session.call_count, 1)

    def test_run_work_loop_runs_eval_every_n_cycles(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_loop.build_work_session',
                return_value={'ok': True, 'output_dir': str(root), 'step_count': 2, 'failed_step_count': 0},
            ) as build_session:
                loop = run_work_loop(
                    profile='fast',
                    output_dir=root,
                    duration_minutes=60,
                    interval_minutes=1,
                    max_cycles=4,
                    eval_every=2,
                    source_pack_every=3,
                    monotonic_fn=clock.monotonic,
                    sleep_fn=clock.sleep,
                )

        self.assertTrue(loop['ok'])
        self.assertEqual([cycle['run_eval'] for cycle in loop['cycles']], [False, True, False, True])
        self.assertEqual([cycle['run_source_pack'] for cycle in loop['cycles']], [False, False, True, False])
        self.assertEqual([call.kwargs['run_eval'] for call in build_session.call_args_list], [False, True, False, True])
        self.assertEqual([call.kwargs['run_source_pack'] for call in build_session.call_args_list], [False, False, True, False])

    def test_run_work_loop_can_disable_dashboard_cycles(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_loop.build_work_session',
                return_value={'ok': True, 'output_dir': str(root), 'step_count': 1, 'failed_step_count': 0},
            ) as build_session:
                loop = run_work_loop(
                    profile='fast',
                    output_dir=root,
                    duration_minutes=60,
                    interval_minutes=1,
                    max_cycles=2,
                    dashboard_every=0,
                    monotonic_fn=clock.monotonic,
                    sleep_fn=clock.sleep,
                )

        self.assertTrue(loop['ok'])
        self.assertEqual([cycle['run_dashboard'] for cycle in loop['cycles']], [False, False])
        self.assertEqual([call.kwargs['run_dashboard'] for call in build_session.call_args_list], [False, False])

    def test_run_work_loop_stops_after_consecutive_failures(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_loop.build_work_session',
                return_value={'ok': False, 'output_dir': str(root), 'step_count': 1, 'failed_step_count': 1},
            ) as build_session:
                loop = run_work_loop(
                    profile='fast',
                    output_dir=root,
                    duration_minutes=60,
                    interval_minutes=1,
                    max_cycles=5,
                    stop_after_consecutive_failures=2,
                    monotonic_fn=clock.monotonic,
                    sleep_fn=clock.sleep,
                )

            self.assertFalse(loop['ok'])
            self.assertEqual(loop['stop_reason'], 'consecutive_failures')
            self.assertEqual(loop['cycle_count'], 2)
            self.assertEqual(loop['failed_cycle_count'], 2)
            self.assertEqual(build_session.call_count, 2)
            self.assertTrue((root / 'work_loop.json').exists())
            self.assertTrue((root / 'events.jsonl').exists())

    def test_run_work_loop_records_cycle_exception_and_writes_summary(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('scripts.work_loop.build_work_session', side_effect=RuntimeError('boom')):
                loop = run_work_loop(
                    profile='fast',
                    output_dir=root,
                    duration_minutes=60,
                    interval_minutes=1,
                    max_cycles=1,
                    monotonic_fn=clock.monotonic,
                    sleep_fn=clock.sleep,
                )

            self.assertFalse(loop['ok'])
            self.assertEqual(loop['cycle_count'], 1)
            self.assertEqual(loop['cycles'][0]['error'], 'boom')
            self.assertTrue((root / 'work_loop.json').exists())
            self.assertIn('"error": "boom"', (root / 'work_loop.json').read_text(encoding='utf-8'))

    def test_work_loop_markdown_lists_cycles_and_stop_reason(self) -> None:
        text = work_loop_markdown(
            {
                'ok': False,
                'stop_reason': 'consecutive_failures',
                'started_at': 'start',
                'completed_at': 'done',
                'output_dir': '/tmp/loop',
                'profile': {'name': 'careful'},
                'cycle_count': 1,
                'failed_cycle_count': 1,
                'consecutive_failure_count': 1,
                'events_path': '/tmp/loop/events.jsonl',
                'cycles': [
                    {
                        'cycle': 1,
                        'ok': False,
                        'run_eval': True,
                        'run_source_pack': False,
                        'run_dashboard': True,
                        'step_count': 3,
                        'failed_step_count': 1,
                        'report': '/tmp/loop/cycles/001/work_session.md',
                    }
                ],
            }
        )

        self.assertIn('# Work Loop', text)
        self.assertIn('- Stop reason: consecutive_failures', text)
        self.assertIn('| 1 | no | yes | no | yes | 3 | 1 | [/tmp/loop/cycles/001/work_session.md]', text)


if __name__ == '__main__':
    unittest.main()
