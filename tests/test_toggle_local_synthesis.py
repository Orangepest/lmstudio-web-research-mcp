from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.toggle_local_synthesis import changed_values, set_local_synthesis


def base_config() -> dict[str, object]:
    return {
        'mcpServers': {
            'other': {'command': '/bin/echo'},
            'web-research': {
                'command': '/venv/bin/python',
                'env': {
                    'LOCAL_LLM_REPORT_SYNTHESIS': 'false',
                    'LOCAL_LLM_CONTRADICTION_REVIEW': 'false',
                    'MCP_COMPACT_RESULTS': 'true',
                },
            },
        }
    }


class ToggleLocalSynthesisTests(unittest.TestCase):
    def test_set_local_synthesis_enables_expected_flags(self) -> None:
        updated = set_local_synthesis(base_config(), enabled=True)
        env = updated['mcpServers']['web-research']['env']

        self.assertEqual(env['LOCAL_LLM_REPORT_SYNTHESIS'], 'true')
        self.assertEqual(env['LOCAL_LLM_CONTRADICTION_REVIEW'], 'true')
        self.assertEqual(env['MCP_COMPACT_RESULTS'], 'true')
        self.assertEqual(env['LOCAL_LLM_BASE_URL'], 'http://127.0.0.1:1234/v1')
        self.assertIn('other', updated['mcpServers'])

    def test_set_local_synthesis_rejects_missing_web_research(self) -> None:
        with self.assertRaises(ValueError):
            set_local_synthesis({'mcpServers': {}}, enabled=True)

    def test_changed_values_reports_only_changed_env_values(self) -> None:
        before = base_config()
        after = set_local_synthesis(before, enabled=True)

        changes = changed_values(before, after)

        self.assertTrue(any('LOCAL_LLM_REPORT_SYNTHESIS' in change for change in changes))
        self.assertTrue(any('LOCAL_LLM_BASE_URL' in change for change in changes))

    def test_cli_preview_does_not_write_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'mcp.json'
            config_path.write_text(json.dumps(base_config()), encoding='utf-8')

            result = subprocess.run(
                [
                    sys.executable,
                    'scripts/toggle_local_synthesis.py',
                    '--enable',
                    str(config_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn('Preview only', result.stdout)
            unchanged = json.loads(config_path.read_text(encoding='utf-8'))
            env = unchanged['mcpServers']['web-research']['env']
            self.assertEqual(env['LOCAL_LLM_REPORT_SYNTHESIS'], 'false')

    def test_cli_apply_writes_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'mcp.json'
            config_path.write_text(json.dumps(base_config()), encoding='utf-8')

            subprocess.run(
                [
                    sys.executable,
                    'scripts/toggle_local_synthesis.py',
                    '--enable',
                    str(config_path),
                    '--apply',
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            changed = json.loads(config_path.read_text(encoding='utf-8'))
            env = changed['mcpServers']['web-research']['env']
            self.assertEqual(env['LOCAL_LLM_REPORT_SYNTHESIS'], 'true')


if __name__ == '__main__':
    unittest.main()
