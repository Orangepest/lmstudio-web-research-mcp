#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_research.jobs import create_research_job, list_research_jobs, update_research_job
from web_research.profiles import list_work_profiles


DEFAULT_JOBS_ROOT = ROOT / '.runtime' / 'research_jobs'


def _print(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description='Manage local research mission jobs.')
    parser.add_argument('--root', type=Path, default=DEFAULT_JOBS_ROOT)
    parser.add_argument('--json', action='store_true')
    subparsers = parser.add_subparsers(dest='command', required=True)

    add = subparsers.add_parser('add', help='Queue a research mission request.')
    add.add_argument('request', nargs='?', default='')
    add.add_argument('--request-file', type=Path, default=None)
    add.add_argument('--profile', choices=[item['name'] for item in list_work_profiles()], default='careful')
    add.add_argument('--priority', type=int, default=0)
    add.add_argument('--tag', action='append', default=[])

    list_cmd = subparsers.add_parser('list', help='List queued research jobs.')
    list_cmd.add_argument('--status', default='')
    list_cmd.add_argument('--limit', type=int, default=20)

    update = subparsers.add_parser('update', help='Update job status metadata.')
    update.add_argument('job_id')
    update.add_argument('--status', default='')
    update.add_argument('--event', default='')
    update.add_argument('--run-id', default='')
    update.add_argument('--message', default='')

    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if args.command == 'add':
        request = args.request_file.read_text(encoding='utf-8') if args.request_file else args.request
        result = create_research_job(root, request=request, profile=args.profile, priority=args.priority, tags=args.tag)
    elif args.command == 'list':
        result = list_research_jobs(root, status=args.status or None, limit=args.limit)
    else:
        result = update_research_job(
            root,
            args.job_id,
            status=args.status or None,
            event=args.event or None,
            run_id=args.run_id or None,
            message=args.message or None,
        )
    _print(result, as_json=args.json)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
