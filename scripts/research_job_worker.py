#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import time
import socket
import sys
import uuid
from pathlib import Path
from typing import Awaitable, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_research.jobs import finish_research_job, lease_next_research_job, load_research_job, mark_research_job_running
from web_research.profiles import get_work_profile


DEFAULT_JOBS_ROOT = ROOT / '.runtime' / 'research_jobs'
Runner = Callable[[dict], Awaitable[dict]]


def _print(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _payload_summary(payload: dict) -> dict:
    quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    return {
        'ok': bool(payload.get('ok')),
        'run_id': payload.get('run_id'),
        'run_path': payload.get('run_path'),
        'final_report_path': payload.get('final_report_path'),
        'report_format': payload.get('report_format'),
        'source_count': len(payload.get('sources', []) or []),
        'evidence_count': len(payload.get('evidence', []) or []),
        'claim_count': len(payload.get('claims', []) or []),
        'failure_count': len(payload.get('failures', []) or []),
        'blocked_source_count': len(payload.get('blocked_sources', []) or []),
        'quality_score': quality.get('score'),
        'quality_label': quality.get('label'),
    }


async def execute_research_job(job: dict) -> dict:
    from mcp_server.server import _run_deep_research

    profile = get_work_profile(str(job.get('profile') or 'careful'))
    return await _run_deep_research(
        str(job.get('request') or ''),
        breadth=profile.research_breadth,
        read_top_per_query=profile.read_top_per_query,
        freshness=None,
        render=profile.render,
        report_format=profile.report_format,
        follow_up_rounds=profile.follow_up_rounds,
    )


async def run_worker_once(
    root: Path,
    *,
    worker_id: str,
    lease_seconds: int = 3600,
    runner: Runner = execute_research_job,
) -> dict:
    lease = lease_next_research_job(root, worker_id=worker_id, lease_seconds=lease_seconds)
    if not lease.get('ok') or not lease.get('leased'):
        return {'ok': bool(lease.get('ok')), 'worked': False, **lease}
    job_id = str(lease['job']['job_id'])
    lease_id = str(lease['lease_id'])
    loaded = load_research_job(root, job_id)
    if not loaded.get('ok'):
        return loaded
    job = loaded['job']
    if str(job.get('status')) == 'cancelled':
        return finish_research_job(
            root,
            job_id,
            lease_id=lease_id,
            status='cancelled',
            event='cancelled_before_start',
            message='Job was cancelled before worker execution.',
        )
    started = mark_research_job_running(root, job_id, lease_id=lease_id, message=f'Worker {worker_id} started research job.')
    if not started.get('ok'):
        return started
    try:
        payload = await runner(job)
    except Exception as exc:  # noqa: BLE001
        failed = finish_research_job(
            root,
            job_id,
            lease_id=lease_id,
            status='failed',
            event='failed',
            message=str(exc),
            result={'ok': False, 'error': str(exc)},
        )
        return {'ok': False, 'worked': True, 'job_id': job_id, 'lease_id': lease_id, 'error': str(exc), 'finish': failed}
    run_id = str(payload.get('run_id') or '')
    summary = _payload_summary(payload)
    completed = finish_research_job(
        root,
        job_id,
        lease_id=lease_id,
        status='completed' if payload.get('ok') else 'failed',
        event='completed' if payload.get('ok') else 'failed_quality_or_execution',
        run_id=run_id or None,
        message='Research job completed.' if payload.get('ok') else str(payload.get('message') or 'Research job did not complete successfully.'),
        result=summary,
    )
    return {
        'ok': bool(payload.get('ok')) and bool(completed.get('ok')),
        'worked': True,
        'job_id': job_id,
        'lease_id': lease_id,
        'run_id': run_id or None,
        'result': summary,
        'finish': completed,
    }


async def run_worker(
    root: Path,
    *,
    worker_id: str,
    lease_seconds: int,
    max_jobs: int,
    runner: Runner = execute_research_job,
) -> dict:
    results = []
    for _index in range(max(1, max_jobs)):
        result = await run_worker_once(root, worker_id=worker_id, lease_seconds=lease_seconds, runner=runner)
        results.append(result)
        if not result.get('worked'):
            break
    return {
        'ok': all(bool(item.get('ok')) for item in results if item.get('worked')) if results else True,
        'worker_id': worker_id,
        'worked_count': sum(1 for item in results if item.get('worked')),
        'results': results,
    }


async def run_worker_watch(
    root: Path,
    *,
    worker_id: str,
    lease_seconds: int,
    poll_seconds: float = 30,
    idle_exit_seconds: float = 0,
    max_jobs: int = 0,
    runner: Runner = execute_research_job,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict:
    poll_seconds = max(0.1, float(poll_seconds))
    idle_exit_seconds = max(0.0, float(idle_exit_seconds))
    max_jobs = max(0, int(max_jobs))
    started_at = monotonic_fn()
    last_work_at = started_at
    results = []
    worked_count = 0
    while True:
        if max_jobs and worked_count >= max_jobs:
            stop_reason = 'max_jobs'
            break
        result = await run_worker_once(root, worker_id=worker_id, lease_seconds=lease_seconds, runner=runner)
        results.append(result)
        if result.get('worked'):
            worked_count += 1
            last_work_at = monotonic_fn()
            continue
        if idle_exit_seconds and monotonic_fn() - last_work_at >= idle_exit_seconds:
            stop_reason = 'idle_exit'
            break
        await sleep_fn(poll_seconds)
    return {
        'ok': all(bool(item.get('ok')) for item in results if item.get('worked')),
        'worker_id': worker_id,
        'worked_count': worked_count,
        'stop_reason': stop_reason,
        'results': results,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run queued local research jobs outside the MCP server process.')
    parser.add_argument('--root', type=Path, default=DEFAULT_JOBS_ROOT)
    parser.add_argument('--worker-id', default=f'{socket.gethostname()}-{uuid.uuid4().hex[:8]}')
    parser.add_argument('--lease-seconds', type=int, default=3600)
    parser.add_argument('--max-jobs', type=int, default=1)
    parser.add_argument('--watch', action='store_true', help='Poll for queued jobs until stopped or idle timeout is reached.')
    parser.add_argument('--poll-seconds', type=float, default=30)
    parser.add_argument('--idle-exit-seconds', type=float, default=0)
    parser.add_argument('--json', action='store_true')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.watch:
        result = asyncio.run(
            run_worker_watch(
                args.root.expanduser().resolve(),
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
                poll_seconds=args.poll_seconds,
                idle_exit_seconds=args.idle_exit_seconds,
                max_jobs=args.max_jobs,
            )
        )
    else:
        result = asyncio.run(
            run_worker(
                args.root.expanduser().resolve(),
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
                max_jobs=args.max_jobs,
            )
        )
    _print(result, as_json=args.json)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
