from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.cleanup_work_loops import cleanup_stale_work_loops


def write_loop(root: Path, name: str, *, in_progress: bool, pid: int | None) -> Path:
    loop = root / name
    loop.mkdir()
    payload = {
        'ok': False,
        'in_progress': in_progress,
        'pid': pid,
        'updated_at': '2026-06-05T00:00:00Z',
        'profile': {'name': 'careful'},
        'cycle_count': 1,
        'failed_cycle_count': 0,
        'stop_reason': 'duration_elapsed',
    }
    (loop / 'work_loop.json').write_text(json.dumps(payload), encoding='utf-8')
    (loop / 'events.jsonl').write_text('start\n', encoding='utf-8')
    return loop


class CleanupWorkLoopsTests(unittest.TestCase):
    def test_cleanup_stale_work_loops_preview_does_not_modify_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = write_loop(root, 'stale', in_progress=True, pid=None)

            result = cleanup_stale_work_loops(root, apply=False)
            payload = json.loads((loop / 'work_loop.json').read_text(encoding='utf-8'))

        self.assertTrue(result['ok'])
        self.assertEqual(result['stale_count'], 1)
        self.assertEqual(result['eligible_count'], 0)
        self.assertEqual(result['cleaned_count'], 0)
        self.assertTrue(payload['in_progress'])

    def test_cleanup_stale_work_loops_apply_skips_missing_pid_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = write_loop(root, 'stale', in_progress=True, pid=None)

            result = cleanup_stale_work_loops(root, apply=True)
            payload = json.loads((loop / 'work_loop.json').read_text(encoding='utf-8'))

        self.assertTrue(result['ok'])
        self.assertEqual(result['stale_count'], 1)
        self.assertEqual(result['eligible_count'], 0)
        self.assertEqual(result['cleaned_count'], 0)
        self.assertTrue(payload['in_progress'])

    def test_cleanup_stale_work_loops_apply_can_close_opted_in_missing_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = write_loop(root, 'stale', in_progress=True, pid=None)

            result = cleanup_stale_work_loops(root, apply=True, loop_ids=['stale'], include_legacy_missing_pid=True)
            payload = json.loads((loop / 'work_loop.json').read_text(encoding='utf-8'))
            events = (loop / 'events.jsonl').read_text(encoding='utf-8')

        self.assertEqual(result['eligible_count'], 1)
        self.assertEqual(result['cleaned_count'], 1)
        self.assertFalse(payload['in_progress'])
        self.assertEqual(payload['stop_reason'], 'stale_interrupted')
        self.assertIn('"event": "stale_cleanup"', events)

    def test_cleanup_stale_work_loops_does_not_close_live_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_loop(root, 'active', in_progress=True, pid=os.getpid())

            result = cleanup_stale_work_loops(root, apply=True)

        self.assertEqual(result['stale_count'], 0)
        self.assertEqual(result['cleaned_count'], 0)

    def test_review_failed_work_loop_preview_does_not_modify_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = write_loop(root, 'failed', in_progress=False, pid=None)

            result = cleanup_stale_work_loops(root, apply=False, review_failed=True, loop_ids=['failed'])
            payload = json.loads((loop / 'work_loop.json').read_text(encoding='utf-8'))

        self.assertTrue(result['ok'])
        self.assertEqual(result['failed_count'], 1)
        self.assertEqual(result['review_eligible_count'], 1)
        self.assertEqual(result['reviewed_count'], 0)
        self.assertNotIn('review', payload)

    def test_review_failed_work_loop_apply_requires_explicit_loop_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_loop(root, 'failed', in_progress=False, pid=None)

            result = cleanup_stale_work_loops(root, apply=True, review_failed=True)

        self.assertFalse(result['ok'])
        self.assertIn('explicit loop_id', result['message'])

    def test_review_failed_work_loop_apply_preserves_failure_and_writes_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = write_loop(root, 'failed', in_progress=False, pid=None)

            result = cleanup_stale_work_loops(
                root,
                apply=True,
                review_failed=True,
                loop_ids=['failed'],
                review_note='reviewed old failed loop',
            )
            payload = json.loads((loop / 'work_loop.json').read_text(encoding='utf-8'))
            events = (loop / 'events.jsonl').read_text(encoding='utf-8')

        self.assertTrue(result['ok'])
        self.assertEqual(result['reviewed_count'], 1)
        self.assertFalse(payload['ok'])
        self.assertFalse(payload['in_progress'])
        self.assertTrue(payload['review']['reviewed'])
        self.assertEqual(payload['review']['reason'], 'acknowledged_failed_work_loop')
        self.assertEqual(payload['review']['note'], 'reviewed old failed loop')
        self.assertIn('"event": "work_loop_reviewed"', events)

    def test_review_failed_work_loop_skips_stale_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = write_loop(root, 'stale', in_progress=True, pid=None)

            result = cleanup_stale_work_loops(root, apply=True, review_failed=True, loop_ids=['stale'])
            payload = json.loads((loop / 'work_loop.json').read_text(encoding='utf-8'))

        self.assertEqual(result['failed_count'], 0)
        self.assertEqual(result['reviewed_count'], 0)
        self.assertNotIn('review', payload)


if __name__ == '__main__':
    unittest.main()
