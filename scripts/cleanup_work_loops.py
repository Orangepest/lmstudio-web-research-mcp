#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.work_dashboard import DEFAULT_WORK_LOOP_ROOT, collect_work_loops
from web_research.eval import utc_timestamp


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def _append_event(path: Path, event: dict[str, Any]) -> None:
    payload = {'timestamp': utc_timestamp(), **event}
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + '\n')


def cleanup_stale_work_loops(
    root: Path,
    *,
    apply: bool = False,
    limit: int = 20,
    loop_ids: list[str] | None = None,
    include_legacy_missing_pid: bool = False,
    review_failed: bool = False,
    review_note: str | None = None,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    loops = collect_work_loops(root, limit=max(1, min(limit, 100)))
    wanted = {str(loop_id) for loop_id in loop_ids or [] if str(loop_id).strip()}
    if apply and review_failed and not wanted:
        return {
            'ok': False,
            'apply': apply,
            'root': str(root),
            'checked_count': len(loops),
            'stale_count': 0,
            'eligible_count': 0,
            'cleaned_count': 0,
            'failed_count': 0,
            'review_failed': review_failed,
            'review_eligible_count': 0,
            'reviewed_count': 0,
            'stale_loops': [],
            'failed_loops': [],
            'message': 'Review writes require an explicit loop_id.',
        }
    stale = [loop for loop in loops if loop.get('stale') and (not wanted or loop.get('id') in wanted)]
    failed = [
        loop
        for loop in loops
        if loop.get('failed_unacknowledged') and not loop.get('stale') and (not wanted or loop.get('id') in wanted)
    ]
    eligible = [
        loop
        for loop in stale
        if loop.get('pid') is not None or include_legacy_missing_pid
    ]
    acknowledgement_eligible = list(failed)
    cleaned: list[dict[str, Any]] = []
    for loop in stale:
        summary_path = Path(str(loop.get('path') or ''))
        event_path = Path(str(loop.get('events_path') or ''))
        item = {
            'id': loop.get('id'),
            'path': str(summary_path),
            'events_path': str(event_path),
            'pid': loop.get('pid'),
            'eligible': loop in eligible,
            'blockers': [] if loop in eligible else ['missing_pid_requires_include_legacy_missing_pid'],
            'cycle_count': loop.get('cycle_count'),
            'failed_cycle_count': loop.get('failed_cycle_count'),
        }
        if apply and item['eligible']:
            payload = _load_json(summary_path)
            if payload:
                payload['in_progress'] = False
                payload['ok'] = False
                payload['updated_at'] = utc_timestamp()
                payload['completed_at'] = payload.get('completed_at') or payload['updated_at']
                payload['stop_reason'] = 'stale_interrupted'
                payload['cleanup'] = {
                    'applied_at': payload['updated_at'],
                    'reason': 'stale_pid_not_running',
                    'previous_pid': loop.get('pid'),
                }
                _write_json(summary_path, payload)
                _append_event(event_path, {'event': 'stale_cleanup', 'reason': 'stale_pid_not_running', 'pid': loop.get('pid')})
                item['cleaned'] = True
            else:
                item['cleaned'] = False
                item['message'] = 'Could not load work_loop.json'
        cleaned.append(item)
    acknowledged: list[dict[str, Any]] = []
    for loop in failed:
        summary_path = Path(str(loop.get('path') or ''))
        event_path = Path(str(loop.get('events_path') or ''))
        item = {
            'id': loop.get('id'),
            'path': str(summary_path),
            'events_path': str(event_path),
            'eligible': loop in acknowledgement_eligible,
            'cycle_count': loop.get('cycle_count'),
            'failed_cycle_count': loop.get('failed_cycle_count'),
        }
        if apply and review_failed and item['eligible']:
            payload = _load_json(summary_path)
            if payload:
                timestamp = utc_timestamp()
                payload['updated_at'] = timestamp
                payload['review'] = {
                    'reviewed': True,
                    'reviewed_at': timestamp,
                    'reason': 'acknowledged_failed_work_loop',
                    'note': review_note or '',
                    'previous_stop_reason': payload.get('stop_reason'),
                    'previous_ok': bool(payload.get('ok')),
                    'failed_cycle_count': loop.get('failed_cycle_count'),
                }
                _write_json(summary_path, payload)
                _append_event(
                    event_path,
                    {
                        'event': 'work_loop_reviewed',
                        'reason': payload['review']['reason'],
                        'note': payload['review']['note'],
                        'failed_cycle_count': loop.get('failed_cycle_count'),
                    },
                )
                item['acknowledged'] = True
            else:
                item['acknowledged'] = False
                item['message'] = 'Could not load work_loop.json'
        acknowledged.append(item)
    return {
        'ok': True,
        'apply': apply,
        'root': str(root),
        'checked_count': len(loops),
        'stale_count': len(stale),
        'eligible_count': len(eligible),
        'cleaned_count': sum(1 for item in cleaned if item.get('cleaned')),
        'failed_count': len(failed),
        'review_failed': review_failed,
        'review_eligible_count': len(acknowledgement_eligible),
        'reviewed_count': sum(1 for item in acknowledged if item.get('acknowledged')),
        'stale_loops': cleaned,
        'failed_loops': acknowledged,
        'message': 'Preview only. No files were changed.' if not apply else 'Work-loop cleanup applied.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Preview or close stale unattended work-loop summaries.')
    parser.add_argument('--root', type=Path, default=DEFAULT_WORK_LOOP_ROOT)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--loop-id', action='append', default=[])
    parser.add_argument('--include-legacy-missing-pid', action='store_true')
    parser.add_argument('--review-failed', action='store_true', help='Mark selected completed failed loops as reviewed.')
    parser.add_argument('--note', default=None)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    result = cleanup_stale_work_loops(
        args.root,
        apply=args.apply,
        limit=args.limit,
        loop_ids=args.loop_id,
        include_legacy_missing_pid=args.include_legacy_missing_pid,
        review_failed=args.review_failed,
        review_note=args.note,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Work loops root: {result['root']}")
        print(f"Checked: {result['checked_count']}")
        print(f"Stale: {result['stale_count']}")
        print(f"Eligible: {result['eligible_count']}")
        print(f"Cleaned: {result['cleaned_count']}")
        print(f"Failed: {result['failed_count']}")
        print(f"Review eligible: {result['review_eligible_count']}")
        print(f"Reviewed: {result['reviewed_count']}")
        for loop in result['stale_loops']:
            print(f"- {loop.get('id')} pid={loop.get('pid')} cycles={loop.get('cycle_count')}")
        for loop in result['failed_loops']:
            print(f"- failed {loop.get('id')} cycles={loop.get('cycle_count')} failed={loop.get('failed_cycle_count')}")
        print(result['message'])
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
