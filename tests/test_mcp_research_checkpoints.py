from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_server.server import (
    _parse_safe_checkpoint_request,
    safe_interrupt_research_checkpoints,
    safe_research_checkpoint_status,
)
from web_research.runs import load_research_run, save_research_run


class MCPResearchCheckpointTests(unittest.TestCase):
    def test_parse_safe_checkpoint_request_accepts_options_and_run_ids(self) -> None:
        parsed = _parse_safe_checkpoint_request(
            """
            interrupted
            limit: 3
            run_id: run-a, run-b
            """
        )

        self.assertEqual(parsed['options']['status'], 'interrupted')
        self.assertEqual(parsed['options']['limit'], '3')
        self.assertEqual(parsed['values'], ['run-a', 'run-b'])

    def test_safe_research_checkpoint_status_lists_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('deep_research', 'checkpoint', {'ok': False}, status='in_progress', root=root)
            with patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root):
                result = safe_research_checkpoint_status('in_progress\nlimit=5')
                explicit = safe_research_checkpoint_status(f"run_id: {saved['run_id']}")

        self.assertTrue(result['ok'])
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['checkpoints'][0]['run_id'], saved['run_id'])
        self.assertEqual(explicit['checkpoint_count'], 1)
        self.assertIn('safe_resume_deep_research', explicit['checkpoints'][0]['resume_tool_call'])

    def test_safe_research_checkpoint_status_rejects_bad_limit(self) -> None:
        result = safe_research_checkpoint_status('limit=nope')

        self.assertFalse(result['ok'])
        self.assertIn('limit must be an integer', result['message'])

    def test_safe_interrupt_research_checkpoints_previews_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('deep_research', 'checkpoint', {'ok': False}, status='in_progress', root=root)
            with patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root):
                result = safe_interrupt_research_checkpoints(f"run_id: {saved['run_id']}")
            loaded = load_research_run(saved['run_id'], root=root)

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertTrue(result['previews'][0]['will_interrupt'])
        self.assertEqual(loaded['run']['status'], 'in_progress')

    def test_safe_interrupt_research_checkpoints_requires_explicit_run_id(self) -> None:
        result = safe_interrupt_research_checkpoints('apply=true')

        self.assertFalse(result['ok'])
        self.assertIn('explicit run_id', result['message'])

    def test_safe_interrupt_research_checkpoints_can_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_research_run('deep_research', 'checkpoint', {'ok': False}, status='in_progress', root=root)
            with patch('mcp_server.server.MCP_RESEARCH_RUNS_ROOT', root):
                result = safe_interrupt_research_checkpoints(f"run_id: {saved['run_id']}\napply=true")
            loaded = load_research_run(saved['run_id'], root=root)

        self.assertTrue(result['ok'])
        self.assertFalse(result['dry_run'])
        self.assertEqual(result['interrupted_count'], 1)
        self.assertEqual(loaded['run']['status'], 'interrupted')
        self.assertTrue(loaded['payload']['interruption']['resume_supported'])


if __name__ == '__main__':
    unittest.main()
