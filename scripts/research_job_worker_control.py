#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOBS_ROOT = ROOT / '.runtime' / 'research_jobs'
DEFAULT_STATE_DIR = ROOT / '.runtime' / 'research_job_worker'
WORKER_SCRIPT = ROOT / 'scripts' / 'research_job_worker.py'


def _json_default(value: object) -> str:
    return str(value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


def pid_alive(pid: object) -> bool:
    try:
        value = int(str(pid).strip())
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def build_worker_command(
    *,
    jobs_root: Path,
    worker_id: str,
    lease_seconds: int,
    max_jobs: int,
    poll_seconds: float,
    idle_exit_seconds: float,
    watch: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(WORKER_SCRIPT),
        '--root',
        str(jobs_root),
        '--worker-id',
        worker_id,
        '--lease-seconds',
        str(lease_seconds),
        '--max-jobs',
        str(max_jobs),
        '--json',
    ]
    if watch:
        command.extend(['--watch', '--poll-seconds', str(poll_seconds), '--idle-exit-seconds', str(idle_exit_seconds)])
    return command


def build_tmux_command(*, session: str, command: list[str], log_path: Path) -> list[str]:
    shell_command = f'cd {shlex.quote(str(ROOT))} && {shlex.join(command)} >> {shlex.quote(str(log_path))} 2>&1'
    return ['tmux', 'new-session', '-d', '-s', session, shell_command]


def status_worker(*, state_dir: Path) -> dict[str, Any]:
    state_path = state_dir / 'worker.json'
    state = _read_json(state_path)
    alive = pid_alive(state.get('pid')) if state else False
    return {
        'ok': True,
        'running': alive,
        'state_path': str(state_path),
        'state': state,
        'log_path': state.get('log_path') if state else str(state_dir / 'worker.log'),
    }


def start_worker(
    *,
    jobs_root: Path,
    state_dir: Path,
    worker_id: str,
    lease_seconds: int,
    max_jobs: int,
    poll_seconds: float,
    idle_exit_seconds: float,
    watch: bool,
    tmux: bool,
    session: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    current = status_worker(state_dir=state_dir)
    if current.get('running'):
        return {'ok': True, 'started': False, 'already_running': True, **current}
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / 'worker.log'
    command = build_worker_command(
        jobs_root=jobs_root,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        max_jobs=max_jobs,
        poll_seconds=poll_seconds,
        idle_exit_seconds=idle_exit_seconds,
        watch=watch,
    )
    state = {
        'worker_id': worker_id,
        'jobs_root': str(jobs_root),
        'state_dir': str(state_dir),
        'log_path': str(log_path),
        'command': command,
        'watch': watch,
        'tmux': tmux,
        'session': session if tmux else None,
    }
    if dry_run:
        if tmux:
            state['tmux_command'] = build_tmux_command(session=session, command=command, log_path=log_path)
        return {'ok': True, 'started': False, 'dry_run': True, 'state': state}
    if tmux:
        tmux_command = build_tmux_command(session=session, command=command, log_path=log_path)
        subprocess.run(tmux_command, cwd=ROOT, check=True)
        state['tmux_command'] = tmux_command
        state['pid'] = None
        _write_json(state_dir / 'worker.json', state)
        return {'ok': True, 'started': True, 'mode': 'tmux', 'state_path': str(state_dir / 'worker.json'), 'state': state}
    with log_path.open('a', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    state['pid'] = process.pid
    _write_json(state_dir / 'worker.json', state)
    return {'ok': True, 'started': True, 'mode': 'process', 'state_path': str(state_dir / 'worker.json'), 'state': state}


def stop_worker(*, state_dir: Path, sig: int = signal.SIGTERM) -> dict[str, Any]:
    status = status_worker(state_dir=state_dir)
    state = status.get('state') if isinstance(status.get('state'), dict) else {}
    pid = state.get('pid')
    if not status.get('running'):
        return {'ok': True, 'stopped': False, 'message': 'Worker is not running.', **status}
    os.kill(int(pid), sig)
    return {'ok': True, 'stopped': True, 'pid': pid, 'state_path': status.get('state_path')}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Start, stop, and inspect the local research job worker.')
    parser.add_argument('--root', type=Path, default=DEFAULT_JOBS_ROOT)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument('--json', action='store_true')
    subparsers = parser.add_subparsers(dest='command', required=True)

    start = subparsers.add_parser('start')
    start.add_argument('--worker-id', default=f'{socket.gethostname()}-{uuid.uuid4().hex[:8]}')
    start.add_argument('--lease-seconds', type=int, default=3600)
    start.add_argument('--max-jobs', type=int, default=0, help='0 means unlimited in watch mode.')
    start.add_argument('--poll-seconds', type=float, default=30)
    start.add_argument('--idle-exit-seconds', type=float, default=0)
    start.add_argument('--once', action='store_true', help='Run one batch instead of watch mode.')
    start.add_argument('--tmux', action='store_true')
    start.add_argument('--session', default='lmstudio-research-worker')
    start.add_argument('--dry-run', action='store_true')

    subparsers.add_parser('status')
    subparsers.add_parser('stop')
    subparsers.add_parser('command')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    jobs_root = args.root.expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve()
    if args.command == 'status':
        result = status_worker(state_dir=state_dir)
    elif args.command == 'stop':
        result = stop_worker(state_dir=state_dir)
    elif args.command == 'command':
        worker_id = f'{socket.gethostname()}-{uuid.uuid4().hex[:8]}'
        command = build_worker_command(
            jobs_root=jobs_root,
            worker_id=worker_id,
            lease_seconds=3600,
            max_jobs=0,
            poll_seconds=30,
            idle_exit_seconds=0,
            watch=True,
        )
        result = {
            'ok': True,
            'command': command,
            'shell': shlex.join(command),
            'tmux_command': build_tmux_command(session='lmstudio-research-worker', command=command, log_path=state_dir / 'worker.log'),
        }
    else:
        result = start_worker(
            jobs_root=jobs_root,
            state_dir=state_dir,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
            max_jobs=args.max_jobs,
            poll_seconds=args.poll_seconds,
            idle_exit_seconds=args.idle_exit_seconds,
            watch=not args.once,
            tmux=args.tmux,
            session=args.session,
            dry_run=args.dry_run,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
