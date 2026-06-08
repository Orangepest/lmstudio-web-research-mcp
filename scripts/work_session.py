#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_research.eval import utc_timestamp
from web_research.profiles import get_work_profile, list_work_profiles


DEFAULT_OUTPUT_ROOT = ROOT / '.runtime' / 'work_sessions'


def _json_default(value: object) -> str:
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


def make_work_session_dir(base: Path) -> Path:
    for _attempt in range(10):
        name = f"{utc_timestamp().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"
        path = base / name
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise RuntimeError('Could not create unique work-session output directory.')


def run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, check=False)
    return {
        'command': command,
        'returncode': completed.returncode,
        'stdout': completed.stdout,
        'stderr': completed.stderr,
    }


def _step_result(name: str, output_dir: Path, command_result: dict[str, Any]) -> dict[str, Any]:
    return {
        'name': name,
        'ok': command_result.get('returncode') == 0,
        'output_dir': str(output_dir),
        **command_result,
    }


def build_work_session(
    *,
    profile: str,
    output_dir: Path,
    dry_run: bool = False,
    run_preflight: bool = True,
    run_dashboard: bool = True,
    run_eval: bool = False,
    run_source_pack: bool = False,
    probe_tools: bool = False,
    stop_on_fail: bool = False,
    preflight_eval_mode: str = 'fixture',
) -> dict[str, Any]:
    if preflight_eval_mode not in {'fixture', 'live'}:
        raise ValueError('preflight_eval_mode must be fixture or live.')
    work_profile = get_work_profile(profile)
    output_dir.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []

    def should_continue() -> bool:
        return not stop_on_fail or all(step.get('ok') for step in steps)

    if run_preflight and should_continue():
        step_dir = output_dir / 'preflight'
        command = [
            sys.executable,
            str(ROOT / 'scripts' / 'work_session_preflight.py'),
            '--profile',
            work_profile.name,
            '--output-dir',
            str(step_dir),
            '--eval-mode',
            preflight_eval_mode,
        ]
        if dry_run:
            command.append('--dry-run')
        if probe_tools:
            command.append('--probe-tools')
        steps.append(_step_result('preflight', step_dir, run_command(command)))

    if run_eval and should_continue():
        step_dir = output_dir / 'eval'
        command = [
            sys.executable,
            str(ROOT / 'scripts' / 'run_research_eval.py'),
            '--profile',
            work_profile.name,
            '--output-dir',
            str(step_dir),
        ]
        steps.append(_step_result('eval', step_dir, run_command(command)))

    if run_source_pack and should_continue():
        step_dir = output_dir / 'source_pack'
        command = [
            sys.executable,
            str(ROOT / 'scripts' / 'build_source_pack.py'),
            '--profile',
            work_profile.name,
            '--output-dir',
            str(step_dir),
        ]
        steps.append(_step_result('source_pack', step_dir, run_command(command)))

    if run_dashboard and should_continue():
        output_path = output_dir / 'work_dashboard.md'
        command = [sys.executable, str(ROOT / 'scripts' / 'work_dashboard.py'), '--output', str(output_path)]
        steps.append(_step_result('dashboard', output_dir, run_command(command)))

    session = {
        'ok': all(step.get('ok') for step in steps),
        'completed_at': utc_timestamp(),
        'output_dir': str(output_dir),
        'profile': work_profile.to_dict(),
        'dry_run': dry_run,
        'stop_on_fail': stop_on_fail,
        'step_count': len(steps),
        'failed_step_count': sum(1 for step in steps if not step.get('ok')),
        'steps': steps,
    }
    _write_json(output_dir / 'work_session.json', session)
    (output_dir / 'work_session.md').write_text(work_session_markdown(session), encoding='utf-8')
    return session


def work_session_markdown(session: dict[str, Any]) -> str:
    profile = session.get('profile') if isinstance(session.get('profile'), dict) else {}
    lines = [
        '# Work Session',
        '',
        f"- Completed at: {session.get('completed_at')}",
        f"- Status: {'pass' if session.get('ok') else 'check'}",
        f"- Profile: {profile.get('name') or 'unknown'}",
        f"- Output dir: {session.get('output_dir')}",
        f"- Steps: {session.get('step_count', 0)}",
        f"- Failed steps: {session.get('failed_step_count', 0)}",
        '',
        '## Steps',
        '',
        '| Step | OK | Return Code | Output |',
        '| --- | --- | ---: | --- |',
    ]
    for step in session.get('steps', []) or []:
        if isinstance(step, dict):
            lines.append(
                '| {name} | {ok} | {code} | {output} |'.format(
                    name=step.get('name', ''),
                    ok='yes' if step.get('ok') else 'no',
                    code=step.get('returncode', ''),
                    output=step.get('output_dir', ''),
                )
            )
    lines.append('')
    return '\n'.join(lines)


def build_work_loop(
    *,
    profile: str,
    output_dir: Path,
    repeat: int | None = 1,
    duration_minutes: float | None = None,
    interval_seconds: float = 0,
    dry_run: bool = False,
    run_preflight: bool = True,
    run_dashboard: bool = True,
    run_eval: bool = False,
    run_source_pack: bool = False,
    probe_tools: bool = False,
    stop_on_fail: bool = False,
    stop_loop_on_fail: bool = False,
    preflight_eval_mode: str = 'fixture',
) -> dict[str, Any]:
    if repeat is not None and repeat < 1:
        raise ValueError('repeat must be at least 1 when provided.')
    if duration_minutes is not None and duration_minutes < 0:
        raise ValueError('duration_minutes must be non-negative when provided.')
    if interval_seconds < 0:
        raise ValueError('interval_seconds must be non-negative.')

    work_profile = get_work_profile(profile)
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_timestamp()
    started_monotonic = time.monotonic()
    deadline = started_monotonic + (duration_minutes * 60) if duration_minutes is not None else None
    iterations: list[dict[str, Any]] = []
    index = 0

    while True:
        if repeat is not None and index >= repeat:
            break
        if deadline is not None and index > 0 and time.monotonic() >= deadline:
            break

        index += 1
        session_dir = output_dir / f'iteration-{index:03d}'
        session = build_work_session(
            profile=work_profile.name,
            output_dir=session_dir,
            dry_run=dry_run,
            run_preflight=run_preflight,
            run_dashboard=run_dashboard,
            run_eval=run_eval,
            run_source_pack=run_source_pack,
            probe_tools=probe_tools,
            stop_on_fail=stop_on_fail,
            preflight_eval_mode=preflight_eval_mode,
        )
        iterations.append(
            {
                'iteration': index,
                'ok': bool(session.get('ok')),
                'output_dir': session.get('output_dir'),
                'report': str(session_dir / 'work_session.md'),
                'failed_step_count': int(session.get('failed_step_count') or 0),
                'step_count': int(session.get('step_count') or 0),
            }
        )
        _write_json(output_dir / 'work_loop.json', _work_loop_summary_payload(
            output_dir=output_dir,
            profile=work_profile.to_dict(),
            started_at=started_at,
            repeat=repeat,
            duration_minutes=duration_minutes,
            interval_seconds=interval_seconds,
            dry_run=dry_run,
            run_eval=run_eval,
            run_source_pack=run_source_pack,
            stop_on_fail=stop_on_fail,
            stop_loop_on_fail=stop_loop_on_fail,
            iterations=iterations,
            in_progress=True,
        ))
        if stop_loop_on_fail and not session.get('ok'):
            break
        should_continue_repeat = repeat is None or index < repeat
        should_continue_duration = deadline is None or time.monotonic() < deadline
        if should_continue_repeat and should_continue_duration and interval_seconds:
            time.sleep(interval_seconds)

    summary = _work_loop_summary_payload(
        output_dir=output_dir,
        profile=work_profile.to_dict(),
        started_at=started_at,
        repeat=repeat,
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
        dry_run=dry_run,
        run_eval=run_eval,
        run_source_pack=run_source_pack,
        stop_on_fail=stop_on_fail,
        stop_loop_on_fail=stop_loop_on_fail,
        iterations=iterations,
        in_progress=False,
    )
    _write_json(output_dir / 'work_loop.json', summary)
    (output_dir / 'work_loop.md').write_text(work_loop_markdown(summary), encoding='utf-8')
    return summary


def _work_loop_summary_payload(
    *,
    output_dir: Path,
    profile: dict[str, Any],
    started_at: str,
    repeat: int | None,
    duration_minutes: float | None,
    interval_seconds: float,
    dry_run: bool,
    run_eval: bool,
    run_source_pack: bool,
    stop_on_fail: bool,
    stop_loop_on_fail: bool,
    iterations: list[dict[str, Any]],
    in_progress: bool,
) -> dict[str, Any]:
    return {
        'ok': bool(iterations) and all(item.get('ok') for item in iterations),
        'in_progress': in_progress,
        'started_at': started_at,
        'updated_at': utc_timestamp(),
        'output_dir': str(output_dir),
        'profile': profile,
        'repeat': repeat,
        'duration_minutes': duration_minutes,
        'interval_seconds': interval_seconds,
        'dry_run': dry_run,
        'run_eval': run_eval,
        'run_source_pack': run_source_pack,
        'stop_on_fail': stop_on_fail,
        'stop_loop_on_fail': stop_loop_on_fail,
        'iteration_count': len(iterations),
        'failed_iteration_count': sum(1 for item in iterations if not item.get('ok')),
        'iterations': iterations,
    }


def work_loop_markdown(loop: dict[str, Any]) -> str:
    profile = loop.get('profile') if isinstance(loop.get('profile'), dict) else {}
    lines = [
        '# Work Loop',
        '',
        f"- Status: {'pass' if loop.get('ok') else 'check'}",
        f"- In progress: {'yes' if loop.get('in_progress') else 'no'}",
        f"- Started at: {loop.get('started_at')}",
        f"- Updated at: {loop.get('updated_at')}",
        f"- Profile: {profile.get('name') or 'unknown'}",
        f"- Output dir: {loop.get('output_dir')}",
        f"- Iterations: {loop.get('iteration_count', 0)}",
        f"- Failed iterations: {loop.get('failed_iteration_count', 0)}",
        f"- Repeat: {loop.get('repeat')}",
        f"- Duration minutes: {loop.get('duration_minutes')}",
        f"- Interval seconds: {loop.get('interval_seconds')}",
        '',
        '## Iterations',
        '',
        '| # | OK | Steps | Failed Steps | Report |',
        '| ---: | --- | ---: | ---: | --- |',
    ]
    for item in loop.get('iterations', []) or []:
        if isinstance(item, dict):
            report = item.get('report', '')
            lines.append(
                '| {iteration} | {ok} | {steps} | {failed} | [{report}]({report}) |'.format(
                    iteration=item.get('iteration', ''),
                    ok='yes' if item.get('ok') else 'no',
                    steps=item.get('step_count', 0),
                    failed=item.get('failed_step_count', 0),
                    report=report,
                )
            )
    lines.append('')
    return '\n'.join(lines)


def _effective_loop_repeat(*, repeat: int, duration_minutes: float | None) -> int | None:
    if duration_minutes is not None and repeat == 1:
        return None
    return repeat


def main() -> int:
    parser = argparse.ArgumentParser(description='Run a profile-driven operational work session.')
    parser.add_argument('--profile', choices=[item['name'] for item in list_work_profiles()], default='careful')
    parser.add_argument('--list-profiles', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--dry-run', action='store_true', help='Pass dry-run through to preflight.')
    parser.add_argument('--probe-tools', action='store_true', help='Force tool probing during preflight.')
    parser.add_argument(
        '--preflight-eval-mode',
        choices=['fixture', 'live'],
        default='fixture',
        help='Eval smoke mode passed through to work_session_preflight.py.',
    )
    parser.add_argument('--skip-preflight', action='store_true')
    parser.add_argument('--skip-dashboard', action='store_true')
    parser.add_argument('--eval', action='store_true', dest='run_eval', help='Run a profile-based eval after preflight.')
    parser.add_argument('--source-pack', action='store_true', help='Build a profile-based source pack from latest runs.')
    parser.add_argument('--stop-on-fail', action='store_true')
    parser.add_argument('--repeat', type=int, default=1, help='Run multiple work-session iterations.')
    parser.add_argument('--duration-minutes', type=float, default=None, help='Keep running iterations until this duration expires.')
    parser.add_argument('--interval-seconds', type=float, default=0, help='Wait between repeated iterations.')
    parser.add_argument('--stop-loop-on-fail', action='store_true', help='Stop repeated iterations after the first failed session.')
    parser.add_argument('--allow-failures', action='store_true', help='Exit 0 even when one or more iterations fail.')
    args = parser.parse_args()
    if args.list_profiles:
        print(json.dumps({'ok': True, 'profiles': list_work_profiles()}, indent=2))
        return 0

    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else make_work_session_dir(DEFAULT_OUTPUT_ROOT)
    if args.repeat > 1 or args.duration_minutes is not None:
        repeat = _effective_loop_repeat(repeat=args.repeat, duration_minutes=args.duration_minutes)
        loop = build_work_loop(
            profile=args.profile,
            output_dir=output_dir,
            repeat=repeat,
            duration_minutes=args.duration_minutes,
            interval_seconds=args.interval_seconds,
            dry_run=args.dry_run,
            run_preflight=not args.skip_preflight,
            run_dashboard=not args.skip_dashboard,
            run_eval=args.run_eval,
            run_source_pack=args.source_pack,
            probe_tools=args.probe_tools,
            stop_on_fail=args.stop_on_fail,
            stop_loop_on_fail=args.stop_loop_on_fail,
            preflight_eval_mode=args.preflight_eval_mode,
        )
        print(
            json.dumps(
                {
                    'ok': loop['ok'],
                    'output_dir': loop['output_dir'],
                    'report': str(output_dir / 'work_loop.md'),
                    'iteration_count': loop['iteration_count'],
                    'failed_iteration_count': loop['failed_iteration_count'],
                },
                indent=2,
            )
        )
        return 0 if loop.get('ok') or args.allow_failures else 1

    session = build_work_session(
        profile=args.profile,
        output_dir=output_dir,
        dry_run=args.dry_run,
        run_preflight=not args.skip_preflight,
        run_dashboard=not args.skip_dashboard,
        run_eval=args.run_eval,
        run_source_pack=args.source_pack,
        probe_tools=args.probe_tools,
        stop_on_fail=args.stop_on_fail,
        preflight_eval_mode=args.preflight_eval_mode,
    )
    print(
        json.dumps(
            {
                'ok': session['ok'],
                'output_dir': session['output_dir'],
                'report': str(output_dir / 'work_session.md'),
                'failed_step_count': session['failed_step_count'],
            },
            indent=2,
        )
    )
    return 0 if session.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
