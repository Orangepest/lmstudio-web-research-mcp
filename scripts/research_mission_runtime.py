#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_research.config import settings
from web_research.mission_runtime import research_mission_runtime


DEFAULT_JOBS_ROOT = ROOT / '.runtime' / 'research_jobs'
DEFAULT_WORKER_STATE_DIR = ROOT / '.runtime' / 'research_job_worker'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='One-command local research mission runtime.')
    parser.add_argument('request', nargs='?', default='status')
    parser.add_argument('--request-file', type=Path, default=None)
    parser.add_argument('--root', type=Path, default=DEFAULT_JOBS_ROOT)
    parser.add_argument('--runs-root', type=Path, default=settings.research_runs_dir)
    parser.add_argument('--worker-state-dir', type=Path, default=DEFAULT_WORKER_STATE_DIR)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    request = args.request_file.read_text(encoding='utf-8') if args.request_file else args.request
    if args.apply and 'apply' not in request.lower():
        request = f'{request}\napply=true'
    result = research_mission_runtime(
        request,
        jobs_root=args.root.expanduser().resolve(),
        runs_root=args.runs_root.expanduser().resolve(),
        worker_state_dir=args.worker_state_dir.expanduser().resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
