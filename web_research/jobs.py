from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


JOB_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,120}$')


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _parse_utc(value: object) -> datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_from_now(seconds: int) -> str:
    return (datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=max(1, seconds))).isoformat().replace('+00:00', 'Z')


def _slug(value: str, *, limit: int = 42) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return slug[:limit].strip('-') or 'research-job'


def _json_default(value: object) -> str:
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f'{path.suffix}.tmp')
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')
    temp_path.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_job_dir(root: Path, job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise ValueError('Invalid research job id')
    base = root.expanduser().resolve()
    job_dir = (base / job_id).resolve()
    if base not in job_dir.parents and job_dir != base:
        raise ValueError('Invalid research job path')
    return job_dir


def create_research_job(
    root: Path,
    *,
    request: str,
    profile: str = 'careful',
    priority: int = 0,
    tags: list[str] | None = None,
    status: str = 'queued',
) -> dict[str, Any]:
    cleaned_request = str(request or '').strip()
    if not cleaned_request:
        return {'ok': False, 'message': 'Research job request is required.'}
    created_at = utc_now()
    nonce = uuid.uuid4().hex[:8]
    job_id = f'{created_at.replace(":", "").replace("-", "")}-{_slug(cleaned_request)}-{nonce}'.lower()
    job_dir = _safe_job_dir(root, job_id)
    job = {
        'job_id': job_id,
        'status': status,
        'request': cleaned_request,
        'profile': str(profile or 'careful'),
        'priority': int(priority),
        'tags': list(tags or []),
        'created_at': created_at,
        'updated_at': created_at,
        'attempt_count': 0,
        'run_ids': [],
        'events': [{'timestamp': created_at, 'event': 'created', 'status': status}],
    }
    job_dir.mkdir(parents=True, exist_ok=False)
    _write_json(job_dir / 'job.json', job)
    return {'ok': True, 'job': summarize_research_job(job), 'job_path': str(job_dir / 'job.json')}


def load_research_job(root: Path, job_id: str) -> dict[str, Any]:
    try:
        job_path = _safe_job_dir(root, job_id) / 'job.json'
    except ValueError as exc:
        return {'ok': False, 'message': str(exc), 'job_id': job_id}
    job = _read_json(job_path)
    if not job:
        return {'ok': False, 'message': f'Research job not found: {job_id}', 'job_id': job_id}
    return {'ok': True, 'job': job, 'job_path': str(job_path)}


def summarize_research_job(job: dict[str, Any]) -> dict[str, Any]:
    events = job.get('events') if isinstance(job.get('events'), list) else []
    return {
        'job_id': job.get('job_id'),
        'status': job.get('status'),
        'profile': job.get('profile'),
        'priority': int(job.get('priority') or 0),
        'created_at': job.get('created_at'),
        'updated_at': job.get('updated_at'),
        'request_preview': str(job.get('request') or '')[:180],
        'attempt_count': int(job.get('attempt_count') or 0),
        'leased_by': job.get('leased_by'),
        'lease_expires_at': job.get('lease_expires_at'),
        'run_ids': list(job.get('run_ids') or []),
        'tags': list(job.get('tags') or []),
        'last_event': events[-1] if events else None,
    }


def list_research_jobs(root: Path, *, status: str | None = None, limit: int = 20) -> dict[str, Any]:
    root = root.expanduser().resolve()
    jobs = []
    if root.exists():
        for path in root.glob('*/job.json'):
            job = _read_json(path)
            if not job:
                continue
            if status and str(job.get('status')) != status:
                continue
            jobs.append(summarize_research_job(job))
    jobs.sort(key=lambda item: (int(item.get('priority') or 0), str(item.get('created_at') or '')), reverse=True)
    limit = max(1, min(limit, 100))
    return {'ok': True, 'jobs': jobs[:limit], 'count': len(jobs[:limit]), 'total_count': len(jobs)}


def lease_next_research_job(root: Path, *, worker_id: str, lease_seconds: int = 3600) -> dict[str, Any]:
    root = root.expanduser().resolve()
    now = datetime.now(UTC).replace(microsecond=0)
    candidates = []
    if root.exists():
        for path in root.glob('*/job.json'):
            job = _read_json(path)
            if not job:
                continue
            status = str(job.get('status') or '')
            lease_expires_at = _parse_utc(job.get('lease_expires_at'))
            expired = lease_expires_at is not None and lease_expires_at <= now
            if status == 'queued' or (status in {'leased', 'running'} and expired):
                candidates.append((int(job.get('priority') or 0), str(job.get('created_at') or ''), path, job, expired))
    if not candidates:
        return {'ok': True, 'leased': False, 'message': 'No queued research jobs available.'}
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _priority, _created_at, path, job, expired = candidates[0]
    lease_id = uuid.uuid4().hex
    now_text = utc_now()
    previous_status = str(job.get('status') or 'queued')
    job['status'] = 'leased'
    job['updated_at'] = now_text
    job['leased_by'] = str(worker_id or 'research-job-worker')
    job['lease_id'] = lease_id
    job['lease_expires_at'] = _utc_from_now(lease_seconds)
    job['attempt_count'] = int(job.get('attempt_count') or 0) + 1
    events = list(job.get('events') or [])
    events.append(
        {
            'timestamp': now_text,
            'event': 'leased',
            'status': 'leased',
            'worker_id': job['leased_by'],
            'lease_id': lease_id,
            'lease_seconds': max(1, int(lease_seconds)),
            'recovered_expired_lease': bool(expired),
            'previous_status': previous_status,
        }
    )
    job['events'] = events
    _write_json(path, job)
    return {'ok': True, 'leased': True, 'job': summarize_research_job(job), 'lease_id': lease_id, 'job_path': str(path)}


def heartbeat_research_job(root: Path, job_id: str, *, lease_id: str, lease_seconds: int = 3600) -> dict[str, Any]:
    loaded = load_research_job(root, job_id)
    if not loaded.get('ok'):
        return loaded
    job = dict(loaded['job'])
    if str(job.get('lease_id') or '') != str(lease_id or ''):
        return {'ok': False, 'message': 'Research job lease does not match.', 'job_id': job_id}
    now = utc_now()
    job['updated_at'] = now
    job['lease_expires_at'] = _utc_from_now(lease_seconds)
    events = list(job.get('events') or [])
    events.append({'timestamp': now, 'event': 'heartbeat', 'status': job.get('status'), 'lease_id': lease_id})
    job['events'] = events
    _write_json(Path(str(loaded['job_path'])), job)
    return {'ok': True, 'job': summarize_research_job(job), 'job_path': loaded['job_path']}


def mark_research_job_running(root: Path, job_id: str, *, lease_id: str, message: str | None = None) -> dict[str, Any]:
    loaded = load_research_job(root, job_id)
    if not loaded.get('ok'):
        return loaded
    job = dict(loaded['job'])
    if str(job.get('lease_id') or '') != str(lease_id or ''):
        return {'ok': False, 'message': 'Research job lease does not match.', 'job_id': job_id}
    now = utc_now()
    job['status'] = 'running'
    job['updated_at'] = now
    events = list(job.get('events') or [])
    event_payload = {'timestamp': now, 'event': 'started', 'status': 'running', 'lease_id': lease_id}
    if message:
        event_payload['message'] = message
    events.append(event_payload)
    job['events'] = events
    _write_json(Path(str(loaded['job_path'])), job)
    return {'ok': True, 'job': summarize_research_job(job), 'job_path': loaded['job_path']}


def finish_research_job(
    root: Path,
    job_id: str,
    *,
    lease_id: str,
    status: str,
    event: str,
    run_id: str | None = None,
    message: str | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in {'completed', 'failed', 'cancelled'}:
        return {'ok': False, 'message': 'finish status must be completed, failed, or cancelled.', 'job_id': job_id}
    loaded = load_research_job(root, job_id)
    if not loaded.get('ok'):
        return loaded
    job = dict(loaded['job'])
    if str(job.get('lease_id') or '') != str(lease_id or ''):
        return {'ok': False, 'message': 'Research job lease does not match.', 'job_id': job_id}
    now = utc_now()
    job['status'] = status
    job['updated_at'] = now
    job['lease_id'] = None
    job['lease_expires_at'] = None
    if run_id:
        run_ids = list(job.get('run_ids') or [])
        if run_id not in run_ids:
            run_ids.append(run_id)
        job['run_ids'] = run_ids
    if result is not None:
        job['result'] = result
    events = list(job.get('events') or [])
    event_payload = {'timestamp': now, 'event': event, 'status': status}
    if message:
        event_payload['message'] = message
    if run_id:
        event_payload['run_id'] = run_id
    events.append(event_payload)
    job['events'] = events
    _write_json(Path(str(loaded['job_path'])), job)
    return {'ok': True, 'job': summarize_research_job(job), 'job_path': loaded['job_path']}


def update_research_job(
    root: Path,
    job_id: str,
    *,
    status: str | None = None,
    event: str | None = None,
    run_id: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    loaded = load_research_job(root, job_id)
    if not loaded.get('ok'):
        return loaded
    job = dict(loaded['job'])
    now = utc_now()
    if status:
        job['status'] = status
    job['updated_at'] = now
    if run_id:
        run_ids = list(job.get('run_ids') or [])
        if run_id not in run_ids:
            run_ids.append(run_id)
        job['run_ids'] = run_ids
    events = list(job.get('events') or [])
    event_payload = {'timestamp': now, 'event': event or 'updated', 'status': job.get('status')}
    if message:
        event_payload['message'] = message
    if run_id:
        event_payload['run_id'] = run_id
    events.append(event_payload)
    job['events'] = events
    _write_json(Path(str(loaded['job_path'])), job)
    return {'ok': True, 'job': summarize_research_job(job), 'job_path': loaded['job_path']}
