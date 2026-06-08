from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOC_PATH = REPO_ROOT / 'docs' / 'lmstudio-research-mode.md'
DEFAULT_OUTPUT_PATH = REPO_ROOT / '.runtime' / 'lmstudio-research-system-prompt.txt'


class PresetError(ValueError):
    pass


def extract_system_prompt(markdown: str) -> str:
    marker = '## System Prompt'
    start = markdown.find(marker)
    if start == -1:
        raise PresetError('System Prompt section not found')

    fence_start = markdown.find('```text', start)
    if fence_start == -1:
        raise PresetError('System Prompt text fence not found')
    prompt_start = markdown.find('\n', fence_start)
    if prompt_start == -1:
        raise PresetError('System Prompt text fence is malformed')
    fence_end = markdown.find('\n```', prompt_start)
    if fence_end == -1:
        raise PresetError('System Prompt closing fence not found')

    prompt = markdown[prompt_start + 1:fence_end].strip()
    if not prompt:
        raise PresetError('System Prompt is empty')
    return prompt


def read_system_prompt(doc_path: Path = DEFAULT_DOC_PATH) -> str:
    return extract_system_prompt(doc_path.read_text(encoding='utf-8'))


def write_prompt_file(prompt: str, output_path: Path = DEFAULT_OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt + '\n', encoding='utf-8')
    return output_path


def build_summary(*, doc_path: Path, output_path: Path, prompt: str, include_prompt: bool) -> str:
    lines = [
        'LM Studio Research Preset',
        '',
        f'Guide: {doc_path}',
        f'Prompt file: {output_path}',
        '',
        'Copy command:',
        f'pbcopy < {output_path}',
        '',
        'LM Studio path:',
        'My Models -> choose model -> Settings/Prompt -> paste as system prompt or preset instructions',
    ]
    if include_prompt:
        lines.extend(['', 'System prompt:', '', prompt])
    return '\n'.join(lines)


def copy_to_clipboard(prompt: str) -> None:
    subprocess.run(['pbcopy'], input=prompt, text=True, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Write and print the LM Studio research-mode system prompt.'
    )
    parser.add_argument('--doc', default=str(DEFAULT_DOC_PATH), help='Research mode markdown guide.')
    parser.add_argument(
        '--output',
        default=str(DEFAULT_OUTPUT_PATH),
        help='Where to write the extracted system prompt.',
    )
    parser.add_argument(
        '--prompt-only',
        action='store_true',
        help='Print only the extracted prompt after writing it.',
    )
    parser.add_argument(
        '--no-prompt',
        action='store_true',
        help='Print paths and copy command without printing the full prompt.',
    )
    parser.add_argument(
        '--copy',
        action='store_true',
        help='Also copy the prompt to the macOS clipboard with pbcopy.',
    )
    args = parser.parse_args()

    doc_path = Path(args.doc).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    prompt = read_system_prompt(doc_path)
    write_prompt_file(prompt, output_path)

    if args.copy:
        copy_to_clipboard(prompt)

    if args.prompt_only:
        print(prompt)
    else:
        print(
            build_summary(
                doc_path=doc_path,
                output_path=output_path,
                prompt=prompt,
                include_prompt=not args.no_prompt,
            )
        )
        if args.copy:
            print('\nCopied to clipboard.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
