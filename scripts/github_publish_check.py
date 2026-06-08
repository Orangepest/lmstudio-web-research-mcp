#!/usr/bin/env python3
"""Run the repository checks that should pass before publishing to GitHub."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IGNORE_DIRS = {
    '.git',
    '.venv',
    '.runtime',
    '.pytest_cache',
    '__pycache__',
    '.hf_upload',
    'dist',
    'build',
    'htmlcov',
}
TEXT_SUFFIXES = {
    '',
    '.cfg',
    '.css',
    '.env',
    '.example',
    '.gitignore',
    '.hfignore',
    '.html',
    '.ini',
    '.json',
    '.lock',
    '.md',
    '.py',
    '.sh',
    '.toml',
    '.txt',
    '.yaml',
    '.yml',
}
SECRET_PATTERN = re.compile(
    r'((api[_-]?key|secret|password|client_secret|access_token|refresh_token|private[_-]?key)\s*[:=]\s*["\']?[^"\'\s]{8,}|'
    r'bearer\s+[a-z0-9._-]{12,}|authorization:\s*bearer)',
    re.IGNORECASE,
)
ABSOLUTE_PERSONAL_PATH_PATTERN = re.compile(r'(/Users/(?!example\b)[^/\s:]+|C:\\Users\\(?!example\b)[^\\\s:]+)')
ALLOWED_SECRET_CONTEXTS = (
    'github_publish_check.py',
    'PUBLISHING.md',
    'test_show_research_preset.py',
)
ALLOWED_PATH_CONTEXT_FILES = (
    'github_publish_check.py',
)
ALLOWED_PATH_CONTEXTS = (
    '/Users/example',
    'C:/ABSOLUTE/PATH',
    'C:\\Users\\example',
)


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def iter_text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob('*'):
        rel_parts = path.relative_to(ROOT).parts
        if any(part in IGNORE_DIRS for part in rel_parts):
            continue
        if not path.is_file():
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in TEXT_SUFFIXES:
            files.append(path)
    return sorted(files)


def scan_files() -> list[str]:
    findings: list[str] = []
    for path in iter_text_files():
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if SECRET_PATTERN.search(line) and not any(token in rel for token in ALLOWED_SECRET_CONTEXTS):
                findings.append(f'{rel}:{line_no}: possible secret marker: {line.strip()[:160]}')
            path_match = ABSOLUTE_PERSONAL_PATH_PATTERN.search(line)
            if (
                path_match
                and not any(token in line for token in ALLOWED_PATH_CONTEXTS)
                and not any(token in rel for token in ALLOWED_PATH_CONTEXT_FILES)
            ):
                findings.append(f'{rel}:{line_no}: personal absolute path: {line.strip()[:160]}')
    return findings


def ignored_required_paths() -> list[str]:
    required = ['.runtime', '.venv', '.env', '.DS_Store']
    missing: list[str] = []
    for item in required:
        result = run(['git', 'check-ignore', '-q', item])
        if result.returncode != 0:
            missing.append(item)
    return missing


def dirty_runtime_entries() -> list[str]:
    result = run(['git', 'ls-files', '--others', '--exclude-standard'])
    if result.returncode != 0:
        return [result.stderr.strip() or 'failed to inspect untracked files']
    return [
        line
        for line in result.stdout.splitlines()
        if line.startswith(('.runtime/', '.venv/', '.pytest_cache/')) or line in {'.DS_Store', '.env'}
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-tests', action='store_true', help='Only run transfer safety checks.')
    args = parser.parse_args()

    failures: list[str] = []

    ignored_missing = ignored_required_paths()
    if ignored_missing:
        failures.append('not ignored: ' + ', '.join(ignored_missing))

    dirty_runtime = dirty_runtime_entries()
    if dirty_runtime:
        failures.append('untracked local/runtime files visible to git: ' + ', '.join(dirty_runtime[:20]))

    scan_findings = scan_files()
    if scan_findings:
        failures.extend(scan_findings)

    if not args.skip_tests:
        pytest_cmd = [sys.executable, '-m', 'pytest', '-q']
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT)
        result = subprocess.run(pytest_cmd, cwd=ROOT, env=env, text=True, check=False)
        if result.returncode != 0:
            failures.append(f'tests failed: {" ".join(pytest_cmd)}')

    if failures:
        print('GitHub publish check: FAILED')
        for finding in failures:
            print(f'- {finding}')
        return 1

    print('GitHub publish check: OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
