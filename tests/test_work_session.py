from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.work_session import (
    _effective_loop_repeat,
    build_work_loop,
    build_work_session,
    make_work_session_dir,
    work_loop_markdown,
    work_session_markdown,
)


class WorkSessionTests(unittest.TestCase):
    def test_make_work_session_dir_creates_unique_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = make_work_session_dir(root)
            second = make_work_session_dir(root)

        self.assertNotEqual(first, second)
        self.assertTrue(first.name)
        self.assertTrue(second.name)

    def test_build_work_session_runs_selected_profile_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_session.run_command',
                return_value={'returncode': 0, 'stdout': '{}', 'stderr': ''},
            ) as run_command:
                session = build_work_session(
                    profile='private-share',
                    output_dir=root,
                    dry_run=True,
                    run_preflight=True,
                    run_dashboard=True,
                    run_eval=True,
                    run_source_pack=True,
                )

            self.assertTrue(session['ok'])
            self.assertEqual(session['profile']['name'], 'private-share')
            self.assertEqual([step['name'] for step in session['steps']], ['preflight', 'eval', 'source_pack', 'dashboard'])
            commands = [' '.join(call.args[0]) for call in run_command.call_args_list]
            self.assertTrue(any('work_session_preflight.py' in command and '--dry-run' in command for command in commands))
            self.assertTrue(any('work_session_preflight.py' in command and '--eval-mode fixture' in command for command in commands))
            self.assertTrue(any('run_research_eval.py' in command and '--profile private-share' in command for command in commands))
            self.assertTrue(any('build_source_pack.py' in command and '--profile private-share' in command for command in commands))
            self.assertTrue((root / 'work_session.json').exists())
            self.assertTrue((root / 'work_session.md').exists())

    def test_build_work_session_can_pass_live_preflight_eval_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_session.run_command',
                return_value={'returncode': 0, 'stdout': '{}', 'stderr': ''},
            ) as run_command:
                build_work_session(
                    profile='careful',
                    output_dir=root,
                    run_preflight=True,
                    run_dashboard=False,
                    preflight_eval_mode='live',
                )

        command = run_command.call_args.args[0]
        self.assertIn('--eval-mode', command)
        self.assertIn('live', command)

    def test_build_work_session_can_stop_after_failed_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_session.run_command',
                return_value={'returncode': 1, 'stdout': '', 'stderr': 'failed'},
            ) as run_command:
                session = build_work_session(
                    profile='fast',
                    output_dir=root,
                    run_preflight=True,
                    run_dashboard=True,
                    run_eval=True,
                    stop_on_fail=True,
                )

        self.assertFalse(session['ok'])
        self.assertEqual(session['failed_step_count'], 1)
        self.assertEqual([step['name'] for step in session['steps']], ['preflight'])
        self.assertEqual(run_command.call_count, 1)

    def test_work_session_markdown_lists_steps(self) -> None:
        text = work_session_markdown(
            {
                'ok': False,
                'completed_at': 'now',
                'output_dir': '/tmp/work',
                'profile': {'name': 'fast'},
                'step_count': 1,
                'failed_step_count': 1,
                'steps': [{'name': 'preflight', 'ok': False, 'returncode': 1, 'output_dir': '/tmp/work/preflight'}],
            }
        )

        self.assertIn('# Work Session', text)
        self.assertIn('- Profile: fast', text)
        self.assertIn('| preflight | no | 1 | /tmp/work/preflight |', text)

    def test_build_work_loop_runs_repeated_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_session.run_command',
                return_value={'returncode': 0, 'stdout': '{}', 'stderr': ''},
            ) as run_command:
                loop = build_work_loop(
                    profile='careful',
                    output_dir=root,
                    repeat=2,
                    interval_seconds=0,
                    dry_run=True,
                    run_preflight=True,
                    run_dashboard=True,
                    run_eval=False,
                    run_source_pack=False,
                )

            self.assertTrue(loop['ok'])
            self.assertEqual(loop['iteration_count'], 2)
            self.assertEqual(loop['failed_iteration_count'], 0)
            self.assertTrue((root / 'iteration-001' / 'work_session.json').exists())
            self.assertTrue((root / 'iteration-002' / 'work_session.json').exists())
            self.assertTrue((root / 'work_loop.json').exists())
            self.assertTrue((root / 'work_loop.md').exists())
            self.assertEqual(run_command.call_count, 4)

    def test_build_work_loop_can_stop_after_failed_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                'scripts.work_session.run_command',
                return_value={'returncode': 1, 'stdout': '', 'stderr': 'failed'},
            ) as run_command:
                loop = build_work_loop(
                    profile='fast',
                    output_dir=root,
                    repeat=3,
                    run_preflight=True,
                    run_dashboard=True,
                    stop_on_fail=True,
                    stop_loop_on_fail=True,
                )

        self.assertFalse(loop['ok'])
        self.assertEqual(loop['iteration_count'], 1)
        self.assertEqual(loop['failed_iteration_count'], 1)
        self.assertEqual(run_command.call_count, 1)

    def test_work_loop_markdown_lists_iterations(self) -> None:
        text = work_loop_markdown(
            {
                'ok': True,
                'in_progress': False,
                'started_at': 'start',
                'updated_at': 'done',
                'output_dir': '/tmp/work-loop',
                'profile': {'name': 'careful'},
                'iteration_count': 1,
                'failed_iteration_count': 0,
                'repeat': 1,
                'duration_minutes': None,
                'interval_seconds': 0,
                'iterations': [
                    {
                        'iteration': 1,
                        'ok': True,
                        'step_count': 2,
                        'failed_step_count': 0,
                        'report': '/tmp/work-loop/iteration-001/work_session.md',
                    }
                ],
            }
        )

        self.assertIn('# Work Loop', text)
        self.assertIn('- Profile: careful', text)
        self.assertIn('| 1 | yes | 2 | 0 | [/tmp/work-loop/iteration-001/work_session.md]', text)

    def test_effective_loop_repeat_treats_duration_as_unbounded_by_default(self) -> None:
        self.assertIsNone(_effective_loop_repeat(repeat=1, duration_minutes=60))
        self.assertEqual(_effective_loop_repeat(repeat=3, duration_minutes=60), 3)
        self.assertEqual(_effective_loop_repeat(repeat=1, duration_minutes=None), 1)


if __name__ == '__main__':
    unittest.main()
