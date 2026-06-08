from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from web_research.jobs import create_research_job, summarize_research_job, utc_now
from web_research.profiles import get_work_profile
from web_research.runs import load_research_run


CAMPAIGN_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,120}$')
VALID_CAMPAIGN_DEPTHS = {'standard', 'deep', 'exhaustive'}
JOB_STATUS_TO_STEP_STATUS = {
    'queued': 'queued',
    'leased': 'running',
    'running': 'running',
    'completed': 'completed',
    'failed': 'failed',
    'cancelled': 'cancelled',
}


def _slug(value: str, *, limit: int = 42) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return slug[:limit].strip('-') or 'research-campaign'


def _json_default(value: object) -> str:
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f'{path.suffix}.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')
    temp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _list_campaign_jobs(jobs_root: Path, campaign_id: object) -> list[dict[str, Any]]:
    campaign_tag = f'campaign:{campaign_id}'
    jobs = []
    root = jobs_root.expanduser().resolve()
    if root.exists():
        for path in root.glob('*/job.json'):
            job = _read_json(path)
            if not job or campaign_tag not in (job.get('tags') or []):
                continue
            jobs.append(summarize_research_job(job))
    jobs.sort(key=lambda item: (int(item.get('priority') or 0), str(item.get('created_at') or '')), reverse=True)
    return jobs


def _safe_campaign_dir(root: Path, campaign_id: str) -> Path:
    if not CAMPAIGN_ID_RE.match(campaign_id):
        raise ValueError('Invalid research campaign id')
    base = root.expanduser().resolve()
    campaign_dir = (base / campaign_id).resolve()
    if base not in campaign_dir.parents and campaign_dir != base:
        raise ValueError('Invalid research campaign path')
    return campaign_dir


def plan_campaign_questions(objective: str, *, depth: str = 'standard') -> list[dict[str, Any]]:
    cleaned = ' '.join(str(objective or '').split())
    if not cleaned:
        return []
    depth = normalize_campaign_depth(depth)
    templates = [
        ('landscape', 'Map the current landscape, key entities, terminology, and recent context for: {objective}'),
        ('primary_sources', 'Find and analyze official, primary, documentation, regulatory, or source-of-truth material for: {objective}'),
        ('evidence', 'Collect strong evidence, data, examples, benchmarks, and case studies relevant to: {objective}'),
        ('risks_limits', 'Identify risks, limitations, failure modes, costs, and counterarguments for: {objective}'),
        ('comparison', 'Compare major alternatives, positions, vendors, approaches, or interpretations related to: {objective}'),
        ('synthesis', 'Synthesize decision-ready findings, unresolved questions, and recommended next steps for: {objective}'),
    ]
    if depth in {'deep', 'exhaustive'}:
        templates.extend(
            [
                ('contradictions', 'Search specifically for contradictions, disputes, retractions, and source disagreements about: {objective}'),
                ('latest', 'Find the newest credible updates, changes, releases, and dated developments about: {objective}'),
                ('implementation', 'Investigate practical implementation details, workflows, tools, constraints, and operational playbooks for: {objective}'),
            ]
        )
    if depth == 'exhaustive':
        templates.extend(
            [
                ('expert_views', 'Gather expert, institutional, academic, or practitioner perspectives on: {objective}'),
                ('open_questions', 'Identify unknowns, weak evidence areas, and follow-up research paths for: {objective}'),
            ]
        )
    return [
        {
            'step_id': f'{index:02d}-{kind}',
            'kind': kind,
            'question': template.format(objective=cleaned),
            'status': 'planned',
            'job_id': None,
            'run_ids': [],
        }
        for index, (kind, template) in enumerate(templates, start=1)
    ]


def normalize_campaign_depth(depth: str | None) -> str:
    value = str(depth or 'standard').strip().lower()
    if value not in VALID_CAMPAIGN_DEPTHS:
        raise ValueError('depth must be standard, deep, or exhaustive.')
    return value


def _campaign_job_step_id(job: dict[str, Any]) -> str | None:
    for tag in job.get('tags') or []:
        text = str(tag)
        if text.startswith('campaign_step:'):
            return text.split(':', 1)[1]
    return None


def _derive_step_state(step: dict[str, Any], jobs_by_step: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    derived = dict(step)
    step_jobs = jobs_by_step.get(str(step.get('step_id') or ''), [])
    if not step_jobs:
        return derived
    run_ids = []
    statuses = []
    job_ids = []
    for job in step_jobs:
        if job.get('job_id'):
            job_ids.append(str(job['job_id']))
        statuses.append(str(job.get('status') or 'queued'))
        for run_id in job.get('run_ids') or []:
            if run_id and run_id not in run_ids:
                run_ids.append(str(run_id))
    if run_ids:
        derived['run_ids'] = run_ids
    if job_ids:
        derived['job_id'] = job_ids[0]
        derived['job_ids'] = job_ids
    if any(status == 'failed' for status in statuses):
        derived['status'] = 'failed'
    elif statuses and all(status == 'completed' for status in statuses):
        derived['status'] = 'completed'
    elif any(status in {'leased', 'running'} for status in statuses):
        derived['status'] = 'running'
    elif any(status == 'cancelled' for status in statuses):
        derived['status'] = 'cancelled'
    elif any(status == 'queued' for status in statuses):
        derived['status'] = 'queued'
    else:
        derived['status'] = JOB_STATUS_TO_STEP_STATUS.get(statuses[0], statuses[0])
    return derived


def summarize_campaign(campaign: dict[str, Any], *, jobs_root: Path | None = None, runs_root: Path | None = None) -> dict[str, Any]:
    raw_steps = campaign.get('steps') if isinstance(campaign.get('steps'), list) else []
    job_summaries = []
    jobs_by_step: dict[str, list[dict[str, Any]]] = {}
    if jobs_root is not None:
        for job in _list_campaign_jobs(jobs_root, campaign.get('campaign_id')):
            job_summaries.append(job)
            step_id = _campaign_job_step_id(job)
            if step_id:
                jobs_by_step.setdefault(step_id, []).append(job)
    steps = [_derive_step_state(step, jobs_by_step) for step in raw_steps if isinstance(step, dict)]
    statuses: dict[str, int] = {}
    run_ids: list[str] = []
    for step in steps:
        status = str(step.get('status') or 'planned')
        statuses[status] = statuses.get(status, 0) + 1
        for run_id in step.get('run_ids') or []:
            if run_id and run_id not in run_ids:
                run_ids.append(str(run_id))
    run_summaries = []
    if runs_root is not None:
        for run_id in run_ids[:20]:
            loaded = load_research_run(run_id, root=runs_root)
            if not loaded.get('ok'):
                continue
            payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
            metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
            quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
            run_summaries.append(
                {
                    'run_id': run_id,
                    'status': metadata.get('status'),
                    'query': metadata.get('query'),
                    'quality_label': quality.get('label'),
                    'quality_score': quality.get('score'),
                    'final_report_path': payload.get('final_report_path'),
                }
            )
    return {
        'campaign_id': campaign.get('campaign_id'),
        'objective': campaign.get('objective'),
        'profile': campaign.get('profile'),
        'depth': campaign.get('depth'),
        'status': campaign.get('status'),
        'created_at': campaign.get('created_at'),
        'updated_at': campaign.get('updated_at'),
        'step_count': len(steps),
        'step_status_counts': statuses,
        'steps': steps,
        'run_ids': run_ids,
        'jobs': job_summaries,
        'runs': run_summaries,
        'campaign_path': campaign.get('campaign_path'),
    }


def create_research_campaign(
    root: Path,
    *,
    objective: str,
    profile: str = 'careful',
    depth: str = 'standard',
    priority: int = 0,
    queue: bool = False,
    jobs_root: Path | None = None,
) -> dict[str, Any]:
    cleaned = ' '.join(str(objective or '').split())
    if not cleaned:
        return {'ok': False, 'message': 'Research campaign objective is required.'}
    profile_obj = get_work_profile(profile)
    try:
        depth = normalize_campaign_depth(depth)
    except ValueError as exc:
        return {'ok': False, 'message': str(exc)}
    if queue and jobs_root is None:
        return {'ok': False, 'message': 'jobs_root is required when queue=true.'}
    now = utc_now()
    campaign_id = f'{now.replace(":", "").replace("-", "")}-{_slug(cleaned)}-{uuid.uuid4().hex[:8]}'.lower()
    campaign_dir = _safe_campaign_dir(root, campaign_id)
    steps = plan_campaign_questions(cleaned, depth=depth)
    campaign = {
        'campaign_id': campaign_id,
        'objective': cleaned,
        'profile': profile_obj.name,
        'depth': depth,
        'priority': int(priority),
        'status': 'queued' if queue else 'planned',
        'created_at': now,
        'updated_at': now,
        'steps': steps,
        'events': [{'timestamp': now, 'event': 'created', 'status': 'queued' if queue else 'planned'}],
    }
    campaign_dir.mkdir(parents=True, exist_ok=False)
    campaign['campaign_path'] = str(campaign_dir / 'campaign.json')
    queued_jobs = []
    if queue:
        for index, step in enumerate(steps, start=1):
            created = create_research_job(
                jobs_root,
                request=str(step['question']),
                profile=profile_obj.name,
                priority=int(priority) + (len(steps) - index),
                tags=[f'campaign:{campaign_id}', f'campaign_step:{step["step_id"]}', f'campaign_depth:{depth}'],
            )
            if created.get('ok'):
                step['status'] = 'queued'
                step['job_id'] = created['job']['job_id']
            queued_jobs.append(created)
        campaign['events'].append({'timestamp': utc_now(), 'event': 'queued_steps', 'status': 'queued', 'job_count': len(queued_jobs)})
    _write_json(campaign_dir / 'campaign.json', campaign)
    return {
        'ok': all(bool(item.get('ok')) for item in queued_jobs) if queued_jobs else True,
        'campaign': summarize_campaign(campaign),
        'campaign_path': str(campaign_dir / 'campaign.json'),
        'queued_jobs': queued_jobs,
    }


def load_research_campaign(root: Path, campaign_id: str) -> dict[str, Any]:
    try:
        path = _safe_campaign_dir(root, campaign_id) / 'campaign.json'
    except ValueError as exc:
        return {'ok': False, 'message': str(exc), 'campaign_id': campaign_id}
    campaign = _read_json(path)
    if not campaign:
        return {'ok': False, 'message': f'Research campaign not found: {campaign_id}', 'campaign_id': campaign_id}
    campaign.setdefault('campaign_path', str(path))
    return {'ok': True, 'campaign': campaign, 'campaign_path': str(path)}


def list_research_campaigns(
    root: Path,
    *,
    limit: int = 20,
    jobs_root: Path | None = None,
    runs_root: Path | None = None,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    campaigns = []
    if root.exists():
        for path in root.glob('*/campaign.json'):
            campaign = _read_json(path)
            if campaign:
                campaign.setdefault('campaign_path', str(path))
                campaigns.append(summarize_campaign(campaign, jobs_root=jobs_root, runs_root=runs_root))
    campaigns.sort(key=lambda item: str(item.get('created_at') or ''), reverse=True)
    limit = max(1, min(100, int(limit)))
    return {'ok': True, 'campaigns': campaigns[:limit], 'count': len(campaigns[:limit]), 'total_count': len(campaigns)}


def parse_campaign_request(request: str) -> dict[str, Any]:
    options: dict[str, str] = {}
    objective_lines: list[str] = []
    values: list[str] = []
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
        if lower in {'queue', 'submit'}:
            options['queue'] = 'true'
        elif lower in {'apply', 'run'}:
            options['apply'] = 'true'
        elif lower in {'status', 'list', 'latest'}:
            options['action'] = lower
        elif lower in {'deep', 'standard', 'exhaustive'}:
            options['depth'] = lower
        elif lower.startswith('campaign_id '):
            values.append(line.split(None, 1)[1].strip())
        else:
            objective_lines.append(line)
    for key in ('objective', 'question', 'query'):
        if key in options:
            objective_lines.insert(0, str(options[key]))
    if 'campaign_id' in options:
        values.insert(0, str(options['campaign_id']))
    return {'options': options, 'objective': ' '.join(objective_lines).strip(), 'values': values}
