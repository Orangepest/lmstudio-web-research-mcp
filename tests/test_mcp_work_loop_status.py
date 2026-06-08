from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_server.server import _parse_safe_work_loop_status_request, safe_work_loop_status
from mcp_server.server import safe_cleanup_work_loops


class MCPWorkLoopStatusTests(unittest.TestCase):
    def test_parse_safe_work_loop_status_request_accepts_options(self) -> None:
        parsed = _parse_safe_work_loop_status_request(
            """
            selector: active
            limit: 3
            loop_id: loop-1
            """
        )

        self.assertEqual(parsed['options']['selector'], 'active')
        self.assertEqual(parsed['options']['limit'], '3')
        self.assertEqual(parsed['values'], ['loop-1'])

    def test_safe_work_loop_status_lists_active_loops_with_event_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / 'loop-active'
            loop.mkdir()
            (loop / 'work_loop.json').write_text(
                json.dumps(
                    {
                        'ok': False,
                        'in_progress': True,
                        'pid': os.getpid(),
                        'updated_at': 'now',
                        'profile': {'name': 'careful'},
                        'cycle_count': 1,
                        'failed_cycle_count': 0,
                        'consecutive_failure_count': 0,
                        'stop_reason': 'duration_elapsed',
                    }
                ),
                encoding='utf-8',
            )
            (loop / 'events.jsonl').write_text('first\nsecond\n', encoding='utf-8')
            with patch('mcp_server.server.MCP_WORK_LOOP_ROOT', root):
                result = safe_work_loop_status('active')

        self.assertTrue(result['ok'])
        self.assertEqual(result['selector'], 'active')
        self.assertEqual(result['loop_count'], 1)
        self.assertEqual(result['loops'][0]['id'], 'loop-active')
        self.assertEqual(result['loops'][0]['event_tail'], ['first', 'second'])

    def test_safe_work_loop_status_can_select_explicit_loop_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('loop-a', 'loop-b'):
                loop = root / name
                loop.mkdir()
                (loop / 'work_loop.json').write_text(
                    json.dumps({'ok': True, 'in_progress': False, 'profile': {'name': 'fast'}, 'cycle_count': 1}),
                    encoding='utf-8',
                )
            with patch('mcp_server.server.MCP_WORK_LOOP_ROOT', root):
                result = safe_work_loop_status('loop_id: loop-b')

        self.assertTrue(result['ok'])
        self.assertEqual(result['selector'], 'explicit')
        self.assertEqual(result['loop_count'], 1)
        self.assertEqual(result['loops'][0]['id'], 'loop-b')

    def test_safe_work_loop_status_rejects_bad_limit(self) -> None:
        result = safe_work_loop_status('limit=nope')

        self.assertFalse(result['ok'])
        self.assertIn('limit must be an integer', result['message'])

    def test_safe_cleanup_work_loops_previews_by_default(self) -> None:
        with patch(
            'mcp_server.server.cleanup_stale_work_loops',
            return_value={'ok': True, 'apply': False, 'stale_count': 1, 'cleaned_count': 0},
        ) as cleanup:
            result = safe_cleanup_work_loops('limit=2')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        cleanup.assert_called_once()
        self.assertFalse(cleanup.call_args.kwargs['apply'])
        self.assertEqual(cleanup.call_args.kwargs['limit'], 2)

    def test_safe_cleanup_work_loops_requires_explicit_loop_id_to_write(self) -> None:
        result = safe_cleanup_work_loops('apply=true\nlimit=1')

        self.assertFalse(result['ok'])
        self.assertIn('explicit loop_id', result['message'])

    def test_safe_cleanup_work_loops_can_apply_explicit_loop_id(self) -> None:
        with patch(
            'mcp_server.server.cleanup_stale_work_loops',
            return_value={'ok': True, 'apply': True, 'stale_count': 1, 'cleaned_count': 1},
        ) as cleanup:
            result = safe_cleanup_work_loops('loop_id: stale\napply=true\ninclude_legacy_missing_pid=true\nlimit=1')

        self.assertTrue(result['ok'])
        self.assertFalse(result['dry_run'])
        self.assertTrue(cleanup.call_args.kwargs['apply'])
        self.assertEqual(cleanup.call_args.kwargs['loop_ids'], ['stale'])
        self.assertTrue(cleanup.call_args.kwargs['include_legacy_missing_pid'])

    def test_safe_cleanup_work_loops_can_preview_failed_review(self) -> None:
        with patch(
            'mcp_server.server.cleanup_stale_work_loops',
            return_value={'ok': True, 'apply': False, 'failed_count': 1, 'reviewed_count': 0},
        ) as cleanup:
            result = safe_cleanup_work_loops('review_failed\nloop_id: failed\nnote: reviewed old failure')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertFalse(cleanup.call_args.kwargs['apply'])
        self.assertTrue(cleanup.call_args.kwargs['review_failed'])
        self.assertEqual(cleanup.call_args.kwargs['loop_ids'], ['failed'])
        self.assertEqual(cleanup.call_args.kwargs['review_note'], 'reviewed old failure')

    def test_safe_cleanup_work_loops_can_apply_failed_review(self) -> None:
        with patch(
            'mcp_server.server.cleanup_stale_work_loops',
            return_value={'ok': True, 'apply': True, 'failed_count': 1, 'reviewed_count': 1},
        ) as cleanup:
            result = safe_cleanup_work_loops('loop_id: failed\nreview_failed=true\napply=true')

        self.assertTrue(result['ok'])
        self.assertFalse(result['dry_run'])
        self.assertTrue(cleanup.call_args.kwargs['apply'])
        self.assertTrue(cleanup.call_args.kwargs['review_failed'])

    def test_safe_cleanup_work_loops_rejects_bad_limit(self) -> None:
        result = safe_cleanup_work_loops('limit=nope')

        self.assertFalse(result['ok'])
        self.assertIn('limit must be an integer', result['message'])


if __name__ == '__main__':
    unittest.main()
