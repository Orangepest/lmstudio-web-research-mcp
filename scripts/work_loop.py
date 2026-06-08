#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.work_session import build_work_session
from web_research.eval import utc_timestamp
from web_research.profiles import get_work_profile, list_work_profiles


DEFAULT_OUTPUT_ROOT = ROOT / '.runtime' / 'work_loops'


def _json_default(value: object) -> str:
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


def _append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'timestamp': utc_timestamp(), **event}
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + '\n')


def make_work_loop_dir(base: Path) -> Path:
    for _attempt in range(10):
        name = f"{utc_timestamp().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"
        path = base / name
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise RuntimeError('Could not create unique work-loop output directory.')


def _cycle_enabled(every: int, cycle: int) -> bool:
    return every > 0 and cycle % every == 0


def _summary_payload(
    *,
    output_dir: Path,
    profile: dict[str, Any],
    started_at: str,
    duration_minutes: float,
    interval_minutes: float,
    max_cycles: int | None,
    dry_run: bool,
    probe_tools: bool,
    eval_every: int,
    source_pack_every: int,
    dashboard_every: int,
    stop_on_fail: bool,
    stop_after_consecutive_failures: int,
    cycles: list[dict[str, Any]],
    consecutive_failure_count: int,
    stop_reason: str,
    in_progress: bool,
) -> dict[str, Any]:
    failed_cycle_count = sum(1 for cycle in cycles if not cycle.get('ok'))
    return {
        'ok': bool(cycles) and failed_cycle_count == 0 and stop_reason not in {'consecutive_failures', 'interrupted'},
        'in_progress': in_progress,
        'started_at': started_at,
        'updated_at': utc_timestamp(),
        'completed_at': None if in_progress else utc_timestamp(),
        'pid': os.getpid(),
        'output_dir': str(output_dir),
        'profile': profile,
        'duration_minutes': duration_minutes,
        'interval_minutes': interval_minutes,
        'max_cycles': max_cycles,
        'dry_run': dry_run,
        'probe_tools': probe_tools,
        'eval_every': eval_every,
        'source_pack_every': source_pack_every,
        'dashboard_every': dashboard_every,
        'stop_on_fail': stop_on_fail,
        'stop_after_consecutive_failures': stop_after_consecutive_failures,
        'cycle_count': len(cycles),
        'failed_cycle_count': failed_cycle_count,
        'consecutive_failure_count': consecutive_failure_count,
        'stop_reason': stop_reason,
        'cycles': cycles,
        'events_path': str(output_dir / 'events.jsonl'),
        'summary_path': str(output_dir / 'work_loop.json'),
        'report_path': str(output_dir / 'work_loop.md'),
    }


def work_loop_markdown(loop: dict[str, Any]) -> str:
    profile = loop.get('profile') if isinstance(loop.get('profile'), dict) else {}
    lines = [
        '# Work Loop',
        '',
        f"- Status: {'pass' if loop.get('ok') else 'check'}",
        f"- Stop reason: {loop.get('stop_reason')}",
        f"- Started at: {loop.get('started_at')}",
        f"- Completed at: {loop.get('completed_at')}",
        f"- Profile: {profile.get('name') or 'unknown'}",
        f"- Output dir: {loop.get('output_dir')}",
        f"- Cycles: {loop.get('cycle_count', 0)}",
        f"- Failed cycles: {loop.get('failed_cycle_count', 0)}",
        f"- Consecutive failures: {loop.get('consecutive_failure_count', 0)}",
        f"- Events: {loop.get('events_path')}",
        '',
        '## Cycles',
        '',
        '| Cycle | OK | Eval | Source Pack | Dashboard | Steps | Failed Steps | Report |',
        '| ---: | --- | --- | --- | --- | ---: | ---: | --- |',
    ]
    for cycle in loop.get('cycles', []) or []:
        if not isinstance(cycle, dict):
            continue
        report = cycle.get('report', '')
        lines.append(
            '| {cycle} | {ok} | {eval} | {pack} | {dash} | {steps} | {failed} | [{report}]({report}) |'.format(
                cycle=cycle.get('cycle', ''),
                ok='yes' if cycle.get('ok') else 'no',
                eval='yes' if cycle.get('run_eval') else 'no',
                pack='yes' if cycle.get('run_source_pack') else 'no',
                dash='yes' if cycle.get('run_dashboard') else 'no',
                steps=cycle.get('step_count', 0),
                failed=cycle.get('failed_step_count', 0),
                report=report,
            )
        )
    lines.append('')
    return '\n'.join(lines)


def run_work_loop(
    *,
    profile: str,
    output_dir: Path,
    duration_minutes: float = 60,
    interval_minutes: float = 10,
    max_cycles: int | None = None,
    dry_run: bool = False,
    probe_tools: bool = False,
    eval_every: int = 0,
    source_pack_every: int = 0,
    dashboard_every: int = 1,
    stop_on_fail: bool = False,
    stop_after_consecutive_failures: int = 2,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if duration_minutes < 0:
        raise ValueError('duration_minutes must be non-negative.')
    if interval_minutes < 0:
        raise ValueError('interval_minutes must be non-negative.')
    if max_cycles is not None and max_cycles < 1:
        raise ValueError('max_cycles must be at least 1 when provided.')
    if eval_every < 0 or source_pack_every < 0 or dashboard_every < 0:
        raise ValueError('every-N flags must be non-negative.')
    if stop_after_consecutive_failures < 1:
        raise ValueError('stop_after_consecutive_failures must be at least 1.')

    work_profile = get_work_profile(profile)
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / 'events.jsonl'
    started_at = utc_timestamp()
    started_monotonic = monotonic_fn()
    deadline = started_monotonic + (duration_minutes * 60)
    cycles: list[dict[str, Any]] = []
    consecutive_failure_count = 0
    stop_reason = 'duration_elapsed'

    def write_summary(*, in_progress: bool) -> dict[str, Any]:
        summary = _summary_payload(
            output_dir=output_dir,
            profile=work_profile.to_dict(),
            started_at=started_at,
            duration_minutes=duration_minutes,
            interval_minutes=interval_minutes,
            max_cycles=max_cycles,
            dry_run=dry_run,
            probe_tools=probe_tools,
            eval_every=eval_every,
            source_pack_every=source_pack_every,
            dashboard_every=dashboard_every,
            stop_on_fail=stop_on_fail,
            stop_after_consecutive_failures=stop_after_consecutive_failures,
            cycles=cycles,
            consecutive_failure_count=consecutive_failure_count,
            stop_reason=stop_reason,
            in_progress=in_progress,
        )
        _write_json(output_dir / 'work_loop.json', summary)
        (output_dir / 'work_loop.md').write_text(work_loop_markdown(summary), encoding='utf-8')
        return summary

    _append_event(events_path, {'event': 'loop_start', 'profile': work_profile.name})
    write_summary(in_progress=True)
    cycle_number = 0
    try:
        while True:
            if max_cycles is not None and cycle_number >= max_cycles:
                stop_reason = 'max_cycles'
                break
            if cycle_number > 0 and monotonic_fn() >= deadline:
                stop_reason = 'duration_elapsed'
                break

            cycle_number += 1
            cycle_dir = output_dir / 'cycles' / f'{cycle_number:03d}'
            run_eval = _cycle_enabled(eval_every, cycle_number)
            run_source_pack = _cycle_enabled(source_pack_every, cycle_number)
            run_dashboard = _cycle_enabled(dashboard_every, cycle_number)
            _append_event(
                events_path,
                {
                    'event': 'cycle_start',
                    'cycle': cycle_number,
                    'run_eval': run_eval,
                    'run_source_pack': run_source_pack,
                    'run_dashboard': run_dashboard,
                    'output_dir': str(cycle_dir),
                },
            )
            cycle_error = None
            try:
                session = build_work_session(
                    profile=work_profile.name,
                    output_dir=cycle_dir,
                    dry_run=dry_run,
                    run_preflight=True,
                    run_dashboard=run_dashboard,
                    run_eval=run_eval,
                    run_source_pack=run_source_pack,
                    probe_tools=probe_tools,
                    stop_on_fail=stop_on_fail,
                )
            except Exception as exc:  # noqa: BLE001
                cycle_error = str(exc)
                session = {
                    'ok': False,
                    'output_dir': str(cycle_dir),
                    'step_count': 0,
                    'failed_step_count': 1,
                    'message': cycle_error,
                }
            cycle = {
                'cycle': cycle_number,
                'ok': bool(session.get('ok')),
                'output_dir': session.get('output_dir'),
                'report': str(cycle_dir / 'work_session.md'),
                'step_count': int(session.get('step_count') or 0),
                'failed_step_count': int(session.get('failed_step_count') or 0),
                'run_eval': run_eval,
                'run_source_pack': run_source_pack,
                'run_dashboard': run_dashboard,
            }
            if cycle_error:
                cycle['error'] = cycle_error
            cycles.append(cycle)
            if cycle['ok']:
                consecutive_failure_count = 0
            else:
                consecutive_failure_count += 1
            _append_event(events_path, {'event': 'cycle_end', **cycle, 'consecutive_failure_count': consecutive_failure_count})
            write_summary(in_progress=True)

            if consecutive_failure_count >= stop_after_consecutive_failures:
                stop_reason = 'consecutive_failures'
                break

            if max_cycles is not None and cycle_number >= max_cycles:
                stop_reason = 'max_cycles'
                break
            remaining_seconds = max(0.0, deadline - monotonic_fn())
            if remaining_seconds <= 0:
                stop_reason = 'duration_elapsed'
                break
            sleep_seconds = min(interval_minutes * 60, remaining_seconds)
            if sleep_seconds:
                _append_event(events_path, {'event': 'sleep', 'seconds': sleep_seconds})
                sleep_fn(sleep_seconds)
    except KeyboardInterrupt:
        stop_reason = 'interrupted'
        _append_event(events_path, {'event': 'interrupted'})

    summary = write_summary(in_progress=False)
    _append_event(events_path, {'event': 'loop_end', 'ok': summary['ok'], 'stop_reason': summary['stop_reason']})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description='Run unattended profile-driven work-session cycles.')
    parser.add_argument('--profile', choices=[item['name'] for item in list_work_profiles()], default='careful')
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--duration-minutes', type=float, default=60)
    parser.add_argument('--interval-minutes', type=float, default=10)
    parser.add_argument('--max-cycles', type=int, default=None)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--probe-tools', action='store_true')
    parser.add_argument('--eval-every', type=int, default=0)
    parser.add_argument('--source-pack-every', type=int, default=0)
    parser.add_argument('--dashboard-every', type=int, default=1, help='Run dashboard every N cycles. Use 0 to disable.')
    parser.add_argument('--stop-on-fail', action='store_true')
    parser.add_argument('--stop-after-consecutive-failures', type=int, default=2)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else make_work_loop_dir(DEFAULT_OUTPUT_ROOT)
    summary = run_work_loop(
        profile=args.profile,
        output_dir=output_dir,
        duration_minutes=args.duration_minutes,
        interval_minutes=args.interval_minutes,
        max_cycles=args.max_cycles,
        dry_run=args.dry_run,
        probe_tools=args.probe_tools,
        eval_every=args.eval_every,
        source_pack_every=args.source_pack_every,
        dashboard_every=args.dashboard_every,
        stop_on_fail=args.stop_on_fail,
        stop_after_consecutive_failures=args.stop_after_consecutive_failures,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    else:
        print(
            json.dumps(
                {
                    'ok': summary['ok'],
                    'output_dir': summary['output_dir'],
                    'report': summary['report_path'],
                    'events': summary['events_path'],
                    'cycle_count': summary['cycle_count'],
                    'failed_cycle_count': summary['failed_cycle_count'],
                    'stop_reason': summary['stop_reason'],
                },
                indent=2,
            )
        )
    if summary.get('stop_reason') == 'interrupted':
        return 130
    return 0 if summary.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
