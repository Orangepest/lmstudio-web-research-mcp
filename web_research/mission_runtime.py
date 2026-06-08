from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.research_job_worker_control import start_worker, status_worker, stop_worker
from web_research.jobs import create_research_job, list_research_jobs
from web_research.profiles import get_work_profile
from web_research.runs import list_research_checkpoints, list_research_runs


def parse_runtime_request(request: str) -> dict[str, Any]:
    options: dict[str, str] = {}
    request_lines: list[str] = []
    for raw_line in str(request or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        option = None
        for separator in ('=', ':'):
            if separator in line:
                key, value = line.split(separator, 1)
                option = (key.strip().lower().replace('-', '_').replace(' ', '_'), value.strip())
                break
        if option is not None:
            key, value = option
            options[key] = value
            continue
        lower = line.lower().replace('-', '_')
        if lower in {'status', 'summary', 'jobs', 'worker', 'checkpoints', 'runs'}:
            options['action'] = lower
        elif lower in {'submit', 'queue'}:
            options['submit'] = 'true'
        elif lower in {'start_worker', 'start'}:
            options['start_worker'] = 'true'
        elif lower in {'stop_worker', 'stop'}:
            options['stop_worker'] = 'true'
        elif lower in {'apply', 'run'}:
            options['apply'] = 'true'
        elif lower in {'dry_run', 'preview'}:
            options['dry_run'] = 'true'
        else:
            request_lines.append(line)
    for key in ('question', 'query', 'request'):
        if key in options:
            request_lines.insert(0, str(options[key]))
    return {'options': options, 'request': ' '.join(request_lines).strip()}


def _bool_option(options: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = options.get(key)
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on', 'apply', 'submit', 'start'}


def _int_option(options: dict[str, Any], key: str, default: int, *, minimum: int = 0, maximum: int = 100) -> int:
    try:
        value = int(str(options.get(key, default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _compact_run_summary(run: dict[str, Any]) -> dict[str, Any]:
    quality = run.get('research_quality') if isinstance(run.get('research_quality'), dict) else {}
    budget = run.get('budget') if isinstance(run.get('budget'), dict) else {}
    checkpoint = run.get('checkpoint') if isinstance(run.get('checkpoint'), dict) else {}
    return {
        'run_id': run.get('run_id'),
        'kind': run.get('kind'),
        'status': run.get('status'),
        'created_at': run.get('created_at'),
        'updated_at': run.get('updated_at'),
        'title': run.get('title') or run.get('query'),
        'quality_label': quality.get('label'),
        'quality_score': quality.get('score'),
        'source_count': run.get('source_count') or budget.get('source_count'),
        'evidence_count': run.get('evidence_count') or budget.get('evidence_count'),
        'claim_count': run.get('claim_count'),
        'final_report_path': run.get('final_report_path'),
        'completed_queries': checkpoint.get('completed_queries'),
        'remaining_queries': checkpoint.get('remaining_queries'),
        'suggested_actions': list(run.get('suggested_actions') or [])[:2],
    }


def build_runtime_status(
    *,
    jobs_root: Path,
    runs_root: Path,
    worker_state_dir: Path,
    limit: int = 5,
) -> dict[str, Any]:
    limit = max(1, min(50, int(limit)))
    jobs = list_research_jobs(jobs_root, limit=limit)
    queued = list_research_jobs(jobs_root, status='queued', limit=limit)
    running = list_research_jobs(jobs_root, status='running', limit=limit)
    leased = list_research_jobs(jobs_root, status='leased', limit=limit)
    completed = list_research_jobs(jobs_root, status='completed', limit=limit)
    failed = list_research_jobs(jobs_root, status='failed', limit=limit)
    checkpoints = list_research_checkpoints(limit=limit, root=runs_root)
    runs = list_research_runs(limit=limit, root=runs_root)
    worker = status_worker(state_dir=worker_state_dir)
    return {
        'ok': bool(jobs.get('ok')) and bool(checkpoints.get('ok')) and bool(runs.get('ok')) and bool(worker.get('ok')),
        'worker': worker,
        'jobs': jobs.get('jobs', []),
        'job_counts': {
            'latest': jobs.get('total_count', jobs.get('count', 0)),
            'queued': queued.get('total_count', queued.get('count', 0)),
            'leased': leased.get('total_count', leased.get('count', 0)),
            'running': running.get('total_count', running.get('count', 0)),
            'completed': completed.get('total_count', completed.get('count', 0)),
            'failed': failed.get('total_count', failed.get('count', 0)),
        },
        'checkpoints': [_compact_run_summary(item) for item in checkpoints.get('checkpoints', [])],
        'checkpoint_count': checkpoints.get('total_count', checkpoints.get('count', 0)),
        'runs': [_compact_run_summary(item) for item in runs.get('runs', [])],
        'run_count': runs.get('total_count', runs.get('count', 0)),
    }


def research_mission_runtime(
    request: str,
    *,
    jobs_root: Path,
    runs_root: Path,
    worker_state_dir: Path,
    dry_run_default: bool = True,
) -> dict[str, Any]:
    parsed = parse_runtime_request(request)
    options = parsed['options']
    mission_request = parsed['request']
    dry_run = _bool_option(options, 'dry_run', default=False) or (
        dry_run_default and not _bool_option(options, 'apply')
    )
    submit = _bool_option(options, 'submit') or _bool_option(options, 'queue')
    start = _bool_option(options, 'start_worker') or _bool_option(options, 'start')
    stop = _bool_option(options, 'stop_worker') or _bool_option(options, 'stop')
    limit = _int_option(options, 'limit', 5, minimum=1, maximum=50)
    profile = get_work_profile(str(options.get('profile') or 'careful'))
    priority = _int_option(options, 'priority', 0, minimum=-100, maximum=100)
    result: dict[str, Any] = {
        'ok': True,
        'tool': 'safe_research_runtime',
        'dry_run': dry_run,
        'request': mission_request,
        'profile': profile.name,
        'jobs_root': str(jobs_root),
        'runs_root': str(runs_root),
        'worker_state_dir': str(worker_state_dir),
        'actions': [],
    }

    if submit:
        planned_job = {'request': mission_request, 'profile': profile.name, 'priority': priority, 'status': 'queued'}
        if not mission_request:
            return {'ok': False, **result, 'message': 'Runtime submit needs a research question/request.'}
        if dry_run:
            result['planned_job'] = planned_job
            result['actions'].append('preview_submit')
        else:
            created = create_research_job(jobs_root, request=mission_request, profile=profile.name, priority=priority, tags=['runtime'])
            result['submitted_job'] = created
            result['actions'].append('submitted_job')
            result['ok'] = bool(result['ok'] and created.get('ok'))

    if start:
        worker_id = str(options.get('worker_id') or 'research-mission-runtime')
        worker = start_worker(
            jobs_root=jobs_root,
            state_dir=worker_state_dir,
            worker_id=worker_id,
            lease_seconds=_int_option(options, 'lease_seconds', 3600, minimum=60, maximum=86400),
            max_jobs=_int_option(options, 'max_jobs', 0, minimum=0, maximum=100),
            poll_seconds=float(options.get('poll_seconds') or 30),
            idle_exit_seconds=float(options.get('idle_exit_seconds') or 0),
            watch=not _bool_option(options, 'once'),
            tmux=_bool_option(options, 'tmux'),
            session=str(options.get('session') or 'lmstudio-research-worker'),
            dry_run=dry_run,
        )
        result['worker_start'] = worker
        result['actions'].append('preview_start_worker' if dry_run else 'started_worker')
        result['ok'] = bool(result['ok'] and worker.get('ok'))

    if stop:
        if dry_run:
            result['worker_stop'] = {'ok': True, 'dry_run': True, 'message': 'Preview only. Add apply=true to stop the worker.'}
        else:
            result['worker_stop'] = stop_worker(state_dir=worker_state_dir)
        result['actions'].append('preview_stop_worker' if dry_run else 'stopped_worker')
        result['ok'] = bool(result['ok'] and result['worker_stop'].get('ok'))

    result['status'] = build_runtime_status(
        jobs_root=jobs_root,
        runs_root=runs_root,
        worker_state_dir=worker_state_dir,
        limit=limit,
    )
    if not result['actions']:
        result['actions'].append('status')
    result['message'] = 'Research mission runtime loaded.'
    if dry_run and any(action.startswith('preview') for action in result['actions']):
        result['message'] = 'Preview only. Add apply=true to perform runtime writes/start/stop actions.'
    return result
