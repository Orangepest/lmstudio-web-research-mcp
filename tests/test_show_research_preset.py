from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mcp_server.debug_tools import extract_tool_info
from scripts.show_research_preset import (
    DEFAULT_DOC_PATH,
    PresetError,
    build_summary,
    extract_system_prompt,
    read_system_prompt,
    write_prompt_file,
)


class ShowResearchPresetTests(unittest.TestCase):
    def test_extract_system_prompt_reads_text_fence(self) -> None:
        markdown = '''
# Guide

## System Prompt

```text
Use safe_deep_research for serious work.
Do not invent citations.
```
'''

        self.assertEqual(
            extract_system_prompt(markdown),
            'Use safe_deep_research for serious work.\nDo not invent citations.',
        )

    def test_extract_system_prompt_rejects_missing_section(self) -> None:
        with self.assertRaises(PresetError):
            extract_system_prompt('# Guide\n')

    def test_current_doc_prompt_mentions_safe_tools(self) -> None:
        prompt = read_system_prompt(DEFAULT_DOC_PATH)

        self.assertIn('safe_research', prompt)
        self.assertIn('safe_deep_research', prompt)
        self.assertIn('<parameter=query>', prompt)

    def test_current_doc_prompt_mentions_every_safe_tool(self) -> None:
        prompt = read_system_prompt(DEFAULT_DOC_PATH)
        safe_tools = sorted(name for name in extract_tool_info() if name.startswith('safe_'))

        missing = [name for name in safe_tools if name not in prompt]

        self.assertEqual(missing, [])

    def test_current_doc_prompt_defaults_to_no_tools_and_stops_disabled_retries(self) -> None:
        prompt = read_system_prompt(DEFAULT_DOC_PATH)

        self.assertIn('Default to answering without tools.', prompt)
        self.assertIn('Tool budget ladder:', prompt)
        self.assertIn('If tools are disabled, unavailable, missing, or a tool call fails because tools are off, stop trying tool calls.', prompt)
        self.assertIn('Never use `safe_deep_research` or `safe_research_mission` for ordinary questions', prompt)
        self.assertNotIn('Use tools when the user asks for current, factual', prompt)
        self.assertNotIn('Use `safe_research` for normal research questions.', prompt)

    def test_write_prompt_file_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'nested' / 'prompt.txt'
            write_prompt_file('prompt body', output)

            self.assertEqual(output.read_text(encoding='utf-8'), 'prompt body\n')

    def test_build_summary_can_hide_prompt_body(self) -> None:
        summary = build_summary(
            doc_path=Path('/tmp/doc.md'),
            output_path=Path('/tmp/prompt.txt'),
            prompt='secret prompt',
            include_prompt=False,
        )

        self.assertIn('pbcopy < /tmp/prompt.txt', summary)
        self.assertNotIn('secret prompt', summary)

    def test_cli_prompt_only_writes_output_and_prints_prompt(self) -> None:
        markdown = '''
## System Prompt

```text
Prompt from fixture.
```
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / 'guide.md'
            output = root / 'prompt.txt'
            doc.write_text(markdown, encoding='utf-8')

            result = subprocess.run(
                [
                    sys.executable,
                    'scripts/show_research_preset.py',
                    '--doc',
                    str(doc),
                    '--output',
                    str(output),
                    '--prompt-only',
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertEqual(result.stdout, 'Prompt from fixture.\n')
            self.assertEqual(output.read_text(encoding='utf-8'), 'Prompt from fixture.\n')


if __name__ == '__main__':
    unittest.main()
