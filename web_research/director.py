from __future__ import annotations

import json
import re
import uuid
import asyncio
import hashlib
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scripts.research_job_worker_control import start_worker, status_worker
from web_research.campaign_synthesis import apply_campaign_narrative_synthesis, build_campaign_synthesis, write_campaign_synthesis_bundle
from web_research.campaigns import create_research_campaign, load_research_campaign, normalize_campaign_depth, parse_campaign_request, plan_campaign_questions, summarize_campaign
from web_research.jobs import create_research_job, update_research_job, utc_now
from web_research.profiles import get_work_profile
from web_research.remediation import build_research_remediation_plan
from web_research.runs import find_research_runs, list_research_checkpoints, load_research_run


DIRECTOR_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,120}$')
QUALITY_THRESHOLDS = {'thin': 30, 'weak': 45, 'borderline': 55, 'moderate': 65, 'strong': 75}
URL_REDACTION_PATTERN = re.compile(r'https?://[^\s)\]>"]+')
LOCAL_PATH_REDACTION_PATTERN = re.compile(r'(?<![\w:])/(?:Users|private|var|tmp|Volumes)/[^\s)\]>"]+')
REMEDIATION_STRATEGY_LEARNING_FILE = 'remediation_strategy_learning.json'
REMEDIATION_STRATEGY_COUNT_KEYS = ('attempts', 'resolved', 'remaining', 'failed', 'no_result', 'pending')
RECOVERY_POLICIES = {
    'manual': {
        'stale_hours': 24,
        'review_waves': True,
        'cancel_stuck_jobs': False,
        'allow_worker_restart': False,
        'allow_checkpoint_resume': False,
    },
    'conservative': {
        'stale_hours': 24,
        'review_waves': True,
        'cancel_stuck_jobs': False,
        'allow_worker_restart': False,
        'allow_checkpoint_resume': False,
    },
    'balanced': {
        'stale_hours': 12,
        'review_waves': True,
        'cancel_stuck_jobs': False,
        'allow_worker_restart': True,
        'allow_checkpoint_resume': True,
    },
    'aggressive': {
        'stale_hours': 2,
        'review_waves': True,
        'cancel_stuck_jobs': True,
        'allow_worker_restart': True,
        'allow_checkpoint_resume': True,
    },
}


def _slug(value: str, *, limit: int = 42) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return slug[:limit].strip('-') or 'research-director'


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f'{path.suffix}.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _redact_text(value: Any) -> str:
    text = URL_REDACTION_PATTERN.sub('[redacted-url]', str(value or ''))
    return LOCAL_PATH_REDACTION_PATTERN.sub('[redacted-path]', text)


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


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


def _safe_director_dir(root: Path, director_id: str) -> Path:
    if not DIRECTOR_ID_RE.match(director_id):
        raise ValueError('Invalid research director id')
    base = root.expanduser().resolve()
    director_dir = (base / director_id).resolve()
    if base not in director_dir.parents and director_dir != base:
        raise ValueError('Invalid research director path')
    return director_dir


def parse_director_request(request: str) -> dict[str, Any]:
    parsed = parse_campaign_request(request)
    options = parsed['options']
    lower_objective = str(parsed.get('objective') or '').strip().lower()
    if lower_objective in {'status', 'list', 'latest', 'advance', 'review', 'synthesize'}:
        options['action'] = lower_objective
        parsed['objective'] = ''
    for key in ('director_id', 'id'):
        if key in options:
            parsed['values'].insert(0, str(options[key]))
    return parsed


def _bool_option(options: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = options.get(key)
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on', 'apply', 'submit', 'run'}


def _int_option(options: dict[str, Any], key: str, default: int, *, minimum: int = 0, maximum: int = 100) -> int:
    try:
        value = int(str(options.get(key, default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _recovery_policy(name: str) -> dict[str, Any]:
    normalized = str(name or 'manual').strip().lower().replace('-', '_')
    if normalized in {'auto', 'default'}:
        normalized = 'balanced'
    base = RECOVERY_POLICIES.get(normalized)
    if not base:
        normalized = 'manual'
        base = RECOVERY_POLICIES[normalized]
    policy = dict(base)
    policy['name'] = normalized
    return policy


def _quality_target_score(target: str) -> int:
    return QUALITY_THRESHOLDS.get(str(target or 'moderate').strip().lower(), QUALITY_THRESHOLDS['moderate'])


def _run_quality(payload: dict[str, Any]) -> dict[str, Any]:
    quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
    answer_readiness = payload.get('answer_readiness') if isinstance(payload.get('answer_readiness'), dict) else {}
    budget = payload.get('budget') if isinstance(payload.get('budget'), dict) else {}
    source_selection = payload.get('source_selection_telemetry') if isinstance(payload.get('source_selection_telemetry'), dict) else {}
    return {
        'label': quality.get('label'),
        'score': int(quality.get('score') or 0),
        'source_label': source_quality.get('label'),
        'source_score': source_quality.get('score'),
        'source_count': budget.get('source_count') or len(payload.get('sources', []) or []),
        'claim_count': len(payload.get('claims', []) or []),
        'answer_ready': bool(answer_readiness.get('ok')) if answer_readiness else None,
        'answer_readiness_label': answer_readiness.get('label') if answer_readiness else None,
        'answer_readiness_score': int(answer_readiness.get('score') or 0) if answer_readiness else None,
        'answer_readiness_blocker_count': len(answer_readiness.get('blockers', []) or []) if answer_readiness else 0,
        'planned_authority_source_count': int(
            source_selection.get('planned_authority_source_count') or budget.get('planned_authority_source_count') or 0
        ),
        'selected_authority_source_count': int(
            source_selection.get('selected_authority_source_count') or budget.get('selected_authority_source_count') or 0
        ),
        'planned_low_value_source_count': int(
            source_selection.get('planned_low_value_source_count') or budget.get('planned_low_value_source_count') or 0
        ),
        'planned_policy_skip_count': int(
            source_selection.get('planned_policy_skip_count') or budget.get('planned_policy_skip_count') or 0
        ),
    }


def _contradiction_issue_count(payload: dict[str, Any]) -> int:
    table = payload.get('contradiction_table') if isinstance(payload.get('contradiction_table'), dict) else {}
    review = payload.get('final_answer_review') if isinstance(payload.get('final_answer_review'), dict) else {}
    contradiction = review.get('contradiction_review') if isinstance(review.get('contradiction_review'), dict) else {}
    return int(table.get('conflicted_claim_count') or contradiction.get('conflicted_claim_count') or 0)


def _source_url(item: dict[str, Any]) -> str:
    for key in ('final_url', 'url', 'requested_url'):
        value = str(item.get(key) or '').strip()
        if value.startswith(('http://', 'https://')):
            return value
    return ''


def _source_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ''


def _append_unique(items: list[dict[str, Any]], seen: set[str], item: dict[str, Any], *, key: str = 'url') -> None:
    value = str(item.get(key) or '').strip()
    if not value or value in seen:
        return
    seen.add(value)
    items.append(item)


def build_director_objective_memory(objective: str, *, runs_root: Path, limit: int = 5) -> dict[str, Any]:
    matches = find_research_runs(objective, limit=limit, root=runs_root)
    if not matches.get('ok'):
        return {'ok': False, 'message': matches.get('message') or 'Could not inspect prior runs.', 'prior_runs': []}
    prior_runs = []
    reusable_sources: list[dict[str, Any]] = []
    avoid_paths: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    seen_avoid: set[str] = set()
    for match in matches.get('runs', []) or []:
        run_id = str(match.get('run_id') or '')
        if not run_id:
            continue
        prior_runs.append(
            {
                'run_id': run_id,
                'query': match.get('query'),
                'status': match.get('status'),
                'match_score': match.get('match_score'),
                'matched_terms': match.get('matched_terms') or [],
                'research_quality': match.get('research_quality'),
            }
        )
        loaded = load_research_run(run_id, root=runs_root)
        if not loaded.get('ok'):
            continue
        payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
        for source in payload.get('sources', []) or []:
            if not isinstance(source, dict):
                continue
            url = _source_url(source)
            if not url:
                continue
            _append_unique(
                reusable_sources,
                seen_sources,
                {
                    'run_id': run_id,
                    'title': source.get('title') or source.get('source') or _source_domain(url),
                    'url': url,
                    'domain': _source_domain(url),
                    'source_id': source.get('source_id'),
                },
            )
        for bucket_name in ('blocked_sources', 'failures'):
            for failure in payload.get(bucket_name, []) or []:
                if not isinstance(failure, dict):
                    continue
                url = _source_url(failure)
                if not url:
                    continue
                _append_unique(
                    avoid_paths,
                    seen_avoid,
                    {
                        'run_id': run_id,
                        'url': url,
                        'domain': _source_domain(url),
                        'reason': failure.get('block_type') or failure.get('message') or bucket_name,
                    },
                )
    return {
        'ok': True,
        'query': objective,
        'prior_runs': prior_runs[:limit],
        'reusable_sources': reusable_sources[:12],
        'avoid_paths': avoid_paths[:12],
        'counts': {
            'prior_runs': len(prior_runs[:limit]),
            'reusable_sources': len(reusable_sources[:12]),
            'avoid_paths': len(avoid_paths[:12]),
        },
    }


def _memory_hint_text(memory: dict[str, Any]) -> str:
    sources = memory.get('reusable_sources') if isinstance(memory.get('reusable_sources'), list) else []
    avoid_paths = memory.get('avoid_paths') if isinstance(memory.get('avoid_paths'), list) else []
    lines = []
    if sources:
        lines.append('Prior research memory - reuse or verify these high-value sources first:')
        for source in sources[:5]:
            title = str(source.get('title') or source.get('domain') or 'source').strip()
            lines.append(f'- {title}: {source.get("url")}')
    if avoid_paths:
        lines.append('Avoid or deprioritize these previously blocked/failed paths unless they are essential:')
        for item in avoid_paths[:5]:
            lines.append(f'- {item.get("url")} ({item.get("reason") or "previous failure"})')
    return '\n'.join(lines)


def _request_with_memory_hints(request: str, memory: dict[str, Any] | None) -> str:
    if not memory:
        return request
    hints = _memory_hint_text(memory)
    if not hints:
        return request
    return f'{request}\n\n{hints}'


def _graph_node(node_id: str, kind: str, label: str, **attrs: Any) -> dict[str, Any]:
    payload = {'id': node_id, 'kind': kind, 'label': label}
    payload.update({key: value for key, value in attrs.items() if value is not None})
    return payload


def _graph_edge(source: str, target: str, relation: str, **attrs: Any) -> dict[str, Any]:
    payload = {'source': source, 'target': target, 'relation': relation}
    payload.update({key: value for key, value in attrs.items() if value is not None})
    return payload


def _add_node(nodes: list[dict[str, Any]], seen: set[str], node: dict[str, Any]) -> None:
    node_id = str(node.get('id') or '')
    if not node_id or node_id in seen:
        return
    seen.add(node_id)
    nodes.append(node)


def _claim_source_ids(claim: dict[str, Any], key: str) -> list[str]:
    values = claim.get(key) if isinstance(claim.get(key), list) else []
    return [str(item) for item in values if str(item).strip()]


def _build_quality_gate(
    *,
    campaign: dict[str, Any],
    run_reviews: list[dict[str, Any]],
    run_payloads: list[dict[str, Any]],
    target_score: int,
    incomplete_steps: list[dict[str, Any]],
    follow_up_candidates: list[dict[str, Any]],
    remaining_budget: int,
) -> dict[str, Any]:
    completed_runs = len(run_reviews)
    scores = [int(review.get('quality', {}).get('score') or 0) for review in run_reviews]
    average_score = round(sum(scores) / len(scores), 1) if scores else 0
    min_score = min(scores) if scores else 0
    source_count = sum(int(review.get('quality', {}).get('source_count') or 0) for review in run_reviews)
    claim_count = sum(int(review.get('quality', {}).get('claim_count') or 0) for review in run_reviews)
    planned_authority_count = sum(int(review.get('quality', {}).get('planned_authority_source_count') or 0) for review in run_reviews)
    selected_authority_count = sum(int(review.get('quality', {}).get('selected_authority_source_count') or 0) for review in run_reviews)
    planned_low_value_count = sum(int(review.get('quality', {}).get('planned_low_value_source_count') or 0) for review in run_reviews)
    planned_policy_skip_count = sum(int(review.get('quality', {}).get('planned_policy_skip_count') or 0) for review in run_reviews)
    missing_primary_runs = sum(1 for review in run_reviews if 'missing_primary_sources' in (review.get('gaps') or []))
    below_target_runs = sum(1 for review in run_reviews if int(review.get('quality', {}).get('score') or 0) < target_score)
    readiness_scores = [
        int(review.get('quality', {}).get('answer_readiness_score') or 0)
        for review in run_reviews
        if review.get('quality', {}).get('answer_readiness_score') is not None
    ]
    average_readiness_score = round(sum(readiness_scores) / len(readiness_scores), 1) if readiness_scores else None
    not_ready_runs = sum(1 for review in run_reviews if review.get('quality', {}).get('answer_ready') is False)
    blocked_readiness_runs = sum(
        1
        for review in run_reviews
        if str(review.get('quality', {}).get('answer_readiness_label') or '') in {'blocked', 'not_ready'}
    )
    contradiction_issues = sum(_contradiction_issue_count(payload) for payload in run_payloads)
    unresolved_contradictions = sum(
        1
        for payload in run_payloads
        if _contradiction_issue_count(payload)
        and not any(
            str(item).strip()
            for item in (payload.get('recommended_next_searches', []) or [])
            if 'contradiction' in str(item).lower() or 'verify' in str(item).lower()
        )
    )
    checks = {
        'has_completed_runs': completed_runs > 0,
        'all_campaign_steps_complete': not incomplete_steps,
        'average_score_meets_target': bool(scores) and average_score >= target_score,
        'no_run_below_target': bool(scores) and below_target_runs == 0,
        'has_sources': source_count > 0,
        'has_claims': claim_count > 0,
        'primary_sources_present': missing_primary_runs == 0,
        'contradictions_have_resolution_path': unresolved_contradictions == 0,
        'answers_ready_to_present': not readiness_scores or not_ready_runs == 0,
    }
    failures = [name for name, ok in checks.items() if not ok]
    can_continue = bool(follow_up_candidates) and remaining_budget > 0
    if not completed_runs or incomplete_steps:
        recommended_action = 'wait'
    elif failures and can_continue:
        recommended_action = 'continue'
    elif failures and not can_continue:
        recommended_action = 'stop_budget_exhausted'
    else:
        recommended_action = 'synthesize'
    return {
        'ok': not failures,
        'recommended_action': recommended_action,
        'checks': checks,
        'failures': failures,
        'target_score': target_score,
        'average_score': average_score,
        'min_score': min_score,
        'completed_run_count': completed_runs,
        'source_count': source_count,
        'claim_count': claim_count,
        'planned_authority_source_count': planned_authority_count,
        'selected_authority_source_count': selected_authority_count,
        'planned_low_value_source_count': planned_low_value_count,
        'planned_policy_skip_count': planned_policy_skip_count,
        'below_target_run_count': below_target_runs,
        'missing_primary_run_count': missing_primary_runs,
        'not_ready_run_count': not_ready_runs,
        'blocked_readiness_run_count': blocked_readiness_runs,
        'average_answer_readiness_score': average_readiness_score,
        'contradiction_issue_count': contradiction_issues,
        'unresolved_contradiction_count': unresolved_contradictions,
        'remaining_followup_budget': remaining_budget,
        'follow_up_candidate_count': len(follow_up_candidates),
    }


def create_research_director(
    root: Path,
    *,
    objective: str,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path | None = None,
    profile: str = 'careful',
    depth: str = 'deep',
    budget_jobs: int = 12,
    quality_target: str = 'strong',
    priority: int = 0,
    queue: bool = True,
) -> dict[str, Any]:
    cleaned = ' '.join(str(objective or '').split())
    if not cleaned:
        return {'ok': False, 'message': 'Research director objective is required.'}
    profile_obj = get_work_profile(profile)
    depth = normalize_campaign_depth(depth)
    budget_jobs = max(1, min(100, int(budget_jobs)))
    objective_memory = build_director_objective_memory(cleaned, runs_root=runs_root, limit=5) if runs_root is not None else {'ok': True, 'prior_runs': [], 'reusable_sources': [], 'avoid_paths': [], 'counts': {'prior_runs': 0, 'reusable_sources': 0, 'avoid_paths': 0}}
    now = utc_now()
    director_id = f'{now.replace(":", "").replace("-", "")}-{_slug(cleaned)}-{uuid.uuid4().hex[:8]}'.lower()
    director_dir = _safe_director_dir(root, director_id)
    campaign = create_research_campaign(
        campaign_root,
        objective=cleaned,
        profile=profile_obj.name,
        depth=depth,
        priority=priority,
        queue=queue,
        jobs_root=jobs_root,
    )
    if not campaign.get('ok'):
        return {'ok': False, 'message': campaign.get('message') or 'Could not create director campaign.', 'campaign': campaign}
    campaign_id = campaign['campaign']['campaign_id']
    initial_job_count = len(campaign.get('queued_jobs') or [])
    director = {
        'director_id': director_id,
        'objective': cleaned,
        'profile': profile_obj.name,
        'depth': depth,
        'budget_jobs': budget_jobs,
        'quality_target': quality_target,
        'quality_target_score': _quality_target_score(quality_target),
        'priority': int(priority),
        'status': 'running' if queue else 'planned',
        'campaign_id': campaign_id,
        'initial_job_count': initial_job_count,
        'followup_job_ids': [],
        'synthesis': None,
        'objective_memory': objective_memory,
        'created_at': now,
        'updated_at': now,
        'events': [{'timestamp': now, 'event': 'created', 'campaign_id': campaign_id, 'initial_job_count': initial_job_count}],
    }
    director_dir.mkdir(parents=True, exist_ok=False)
    director['director_path'] = str(director_dir / 'director.json')
    _write_json(director_dir / 'director.json', director)
    return {'ok': True, 'director': director, 'campaign': campaign.get('campaign'), 'queued_jobs': campaign.get('queued_jobs') or []}


def load_research_director(root: Path, director_id: str) -> dict[str, Any]:
    try:
        path = _safe_director_dir(root, director_id) / 'director.json'
    except ValueError as exc:
        return {'ok': False, 'message': str(exc), 'director_id': director_id}
    director = _read_json(path)
    if not director:
        return {'ok': False, 'message': f'Research director not found: {director_id}', 'director_id': director_id}
    director.setdefault('director_path', str(path))
    return {'ok': True, 'director': director, 'director_path': str(path)}


def list_research_directors(root: Path, *, limit: int = 20) -> dict[str, Any]:
    root = root.expanduser().resolve()
    directors = []
    if root.exists():
        for path in root.glob('*/director.json'):
            director = _read_json(path)
            if director:
                director.setdefault('director_path', str(path))
                directors.append(_director_summary(director))
    directors.sort(key=lambda item: str(item.get('created_at') or ''), reverse=True)
    limit = max(1, min(100, int(limit)))
    return {'ok': True, 'directors': directors[:limit], 'count': len(directors[:limit]), 'total_count': len(directors)}


def _director_summary(director: dict[str, Any]) -> dict[str, Any]:
    memory = director.get('objective_memory') if isinstance(director.get('objective_memory'), dict) else {}
    return {
        'director_id': director.get('director_id'),
        'objective': director.get('objective'),
        'status': director.get('status'),
        'campaign_id': director.get('campaign_id'),
        'profile': director.get('profile'),
        'depth': director.get('depth'),
        'budget_jobs': director.get('budget_jobs'),
        'initial_job_count': director.get('initial_job_count'),
        'followup_job_count': len(director.get('followup_job_ids') or []),
        'quality_target': director.get('quality_target'),
        'created_at': director.get('created_at'),
        'updated_at': director.get('updated_at'),
        'synthesis': director.get('synthesis'),
        'objective_memory_counts': memory.get('counts') if isinstance(memory.get('counts'), dict) else None,
        'director_path': director.get('director_path'),
    }


def _director_followup_tags(director: dict[str, Any], director_id: str, candidate: dict[str, Any]) -> list[str]:
    reason = str(candidate.get('reason') or 'unknown')
    tags = [
        f'director:{director_id}',
        f"campaign:{director.get('campaign_id')}",
        'director_followup',
        f'director_reason:{reason}',
    ]
    run_id = str(candidate.get('run_id') or '').strip()
    if run_id:
        tags.append(f'remediates_run:{run_id}')
    if reason.startswith('evidence_remediation:') or reason.startswith('remediation_upgrade:'):
        gap_code = reason.split(':', 1)[1].strip() or 'unknown'
        tags.append(f'remediation_gap:{gap_code}')
    if reason.startswith('remediation_upgrade:'):
        tags.append('remediation_upgrade')
        strategy = str(candidate.get('strategy') or '').strip()
        if strategy:
            tags.append(f'remediation_strategy:{strategy}')
    return tags


def _gap_codes_from_payload(payload: dict[str, Any]) -> set[str]:
    plan = payload.get('remediation_plan') if isinstance(payload.get('remediation_plan'), dict) else build_research_remediation_plan(payload)
    return {
        str(gap.get('code') or '').strip()
        for gap in plan.get('gaps', []) or []
        if isinstance(gap, dict) and str(gap.get('code') or '').strip()
    }


def _director_remediation_outcomes(director: dict[str, Any], *, jobs_root: Path, runs_root: Path) -> dict[str, Any]:
    director_id = str(director.get('director_id') or '')
    root = jobs_root.expanduser().resolve()
    outcomes: list[dict[str, Any]] = []
    counts = {'pending': 0, 'resolved': 0, 'remaining': 0, 'failed': 0, 'no_result': 0}
    if not root.exists() or not director_id:
        return {'counts': counts, 'outcomes': outcomes}
    for job_path in root.glob('*/job.json'):
        job = _read_json(job_path)
        if not job:
            continue
        tags = [str(tag) for tag in job.get('tags', []) or []]
        if f'director:{director_id}' not in tags or 'director_followup' not in tags:
            continue
        gap_tags = [tag.split(':', 1)[1] for tag in tags if tag.startswith('remediation_gap:')]
        if not gap_tags:
            continue
        target_gap = gap_tags[0]
        status = str(job.get('status') or '')
        base = {
            'job_id': job.get('job_id'),
            'status': status,
            'target_gap': target_gap,
            'request': str(job.get('request') or '')[:240],
            'source_run_id': next((tag.split(':', 1)[1] for tag in tags if tag.startswith('remediates_run:')), None),
            'is_upgrade': 'remediation_upgrade' in tags,
            'strategy': next((tag.split(':', 1)[1] for tag in tags if tag.startswith('remediation_strategy:')), None),
            'run_ids': list(job.get('run_ids') or []),
        }
        if status in {'queued', 'leased', 'running'}:
            base['outcome'] = 'pending'
            counts['pending'] += 1
            outcomes.append(base)
            continue
        if status in {'failed', 'cancelled'}:
            base['outcome'] = 'failed'
            counts['failed'] += 1
            outcomes.append(base)
            continue
        if not job.get('run_ids'):
            base['outcome'] = 'no_result'
            counts['no_result'] += 1
            outcomes.append(base)
            continue
        remaining = False
        new_gap_codes: set[str] = set()
        source_count = 0
        for run_id in job.get('run_ids') or []:
            loaded = load_research_run(str(run_id), root=runs_root)
            if not loaded.get('ok'):
                continue
            payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
            source_count += len(payload.get('sources', []) or [])
            gap_codes = _gap_codes_from_payload(payload)
            new_gap_codes.update(gap_codes)
            if target_gap in gap_codes:
                remaining = True
        if remaining:
            base['outcome'] = 'remaining'
            counts['remaining'] += 1
        else:
            base['outcome'] = 'resolved'
            counts['resolved'] += 1
        base['source_count'] = source_count
        base['remaining_gap_codes'] = sorted(new_gap_codes)
        outcomes.append(base)
    return {'counts': counts, 'outcomes': outcomes[:50]}


def _upgrade_base_query(outcome: dict[str, Any], runs_root: Path) -> str:
    source_run_id = str(outcome.get('source_run_id') or '').strip()
    if source_run_id:
        loaded = load_research_run(source_run_id, root=runs_root)
        metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
        query = str(metadata.get('query') or '').strip()
        if query:
            return query
        payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
        payload_query = str(payload.get('question') or payload.get('query') or '').strip()
        if payload_query:
            return payload_query
    return str(outcome.get('request') or '').strip()


def _upgrade_strategy_options(gap_code: str) -> list[dict[str, Any]]:
    options = {
        'missing_primary': (
            {
                'strategy': 'primary_source_only',
                'suffix': 'primary source only official documentation official report source data',
                'explanation': 'Switch to primary-source-only wording after the prior repair still lacked primary evidence.',
                'priority': 118,
            },
            {
                'strategy': 'official_site_search',
                'suffix': 'site:gov OR site:edu OR official site source report documentation',
                'explanation': 'Constrain discovery toward official institutional sites and source documents.',
                'priority': 113,
            },
            {
                'strategy': 'authority_database',
                'suffix': 'official database filing registry dataset source record',
                'explanation': 'Look for database, filing, registry, or dataset records instead of articles.',
                'priority': 111,
            },
        ),
        'authority_candidates_not_selected': (
            {
                'strategy': 'authority_only',
                'suffix': 'primary source only official report dataset filing source data',
                'explanation': 'Force authority-source constraints because prior planned authority sources were not selected.',
                'priority': 116,
            },
            {
                'strategy': 'source_record_search',
                'suffix': 'official registry dataset filing archive source record',
                'explanation': 'Search for source records directly when authority candidates failed selection.',
                'priority': 111,
            },
        ),
        'seo_heavy_source_mix': (
            {
                'strategy': 'anti_seo_authority',
                'suffix': 'official data report filing benchmark exclude guide agency marketing blog',
                'explanation': 'Replace SEO-heavy candidates with official data, filings, reports, or benchmarks.',
                'priority': 114,
            },
            {
                'strategy': 'analyst_primary_mix',
                'suffix': 'official data analyst report benchmark dataset methodology',
                'explanation': 'Blend primary data with reputable analyst or benchmark methodology sources.',
                'priority': 108,
            },
        ),
        'domain_diversity_low': (
            {
                'strategy': 'domain_diversification',
                'suffix': 'independent corroborating sources different domains comparison evidence',
                'explanation': 'Force independent corroboration because the previous repair stayed domain-concentrated.',
                'priority': 110,
            },
            {
                'strategy': 'cross_source_corroboration',
                'suffix': 'independent verification multiple publishers primary source corroboration',
                'explanation': 'Seek corroboration across unrelated publishers and primary sources.',
                'priority': 106,
            },
        ),
        'repeated_domains': (
            {
                'strategy': 'domain_diversification',
                'suffix': 'independent source not same domain corroborating evidence',
                'explanation': 'Diversify away from repeated domains instead of retrying the same source path.',
                'priority': 108,
            },
            {
                'strategy': 'cross_source_corroboration',
                'suffix': 'corroborating evidence from unrelated sources official independent',
                'explanation': 'Find unrelated corroborating evidence for repeated-domain claims.',
                'priority': 104,
            },
        ),
        'single_source_claims': (
            {
                'strategy': 'claim_corroboration',
                'suffix': 'corroborating evidence multiple independent sources source documents',
                'explanation': 'Seek multi-source corroboration for claims that remained weakly supported.',
                'priority': 110,
            },
            {
                'strategy': 'primary_plus_independent',
                'suffix': 'primary source independent confirmation evidence',
                'explanation': 'Pair a primary source with independent confirmation.',
                'priority': 107,
            },
        ),
        'unresolved_conflicts': (
            {
                'strategy': 'conflict_resolution',
                'suffix': 'conflicting evidence comparison official clarification independent verification',
                'explanation': 'Escalate conflict resolution with official clarification and independent verification terms.',
                'priority': 118,
            },
            {
                'strategy': 'timeline_reconciliation',
                'suffix': 'timeline update correction clarification source chronology',
                'explanation': 'Resolve conflicts by checking chronology, corrections, and updates.',
                'priority': 112,
            },
        ),
        'citation_gaps': (
            {
                'strategy': 'citation_repair',
                'suffix': 'directly citable supporting evidence official source quote source document',
                'explanation': 'Target directly citable evidence because citation gaps persisted.',
                'priority': 114,
            },
            {
                'strategy': 'source_document_search',
                'suffix': 'source document PDF report transcript evidence citation',
                'explanation': 'Prefer source documents that can be cited directly.',
                'priority': 109,
            },
        ),
        'freshness_gap': (
            {
                'strategy': 'freshness_repair',
                'suffix': 'latest 2026 update release notes changelog official announcement',
                'explanation': 'Escalate to explicit freshness evidence for current-sensitive claims.',
                'priority': 108,
            },
            {
                'strategy': 'recent_primary_update',
                'suffix': '2026 official announcement current status release note',
                'explanation': 'Look for recent primary updates and current status pages.',
                'priority': 106,
            },
        ),
        'blocked_sources': (
            {
                'strategy': 'accessibility_alternative',
                'suffix': 'accessible official pdf alternate source mirror source document',
                'explanation': 'Switch to accessible alternates after blocked sources prevented repair.',
                'priority': 104,
            },
            {
                'strategy': 'text_mirror_search',
                'suffix': 'text version PDF mirror archived official source',
                'explanation': 'Search for text mirrors or archives for blocked source documents.',
                'priority': 101,
            },
        ),
        'source_policy_skips': (
            {
                'strategy': 'policy_alternative',
                'suffix': 'accessible official source alternative primary source document',
                'explanation': 'Replace policy-skipped sources with fetchable official alternatives.',
                'priority': 102,
            },
            {
                'strategy': 'trusted_source_replacement',
                'suffix': 'trusted source official report dataset accessible',
                'explanation': 'Replace skipped domains with trusted accessible sources.',
                'priority': 100,
            },
        ),
        'read_failures': (
            {
                'strategy': 'fetchable_format',
                'suffix': 'official accessible pdf source document cached text version',
                'explanation': 'Prefer fetchable source formats after repeated read failures.',
                'priority': 104,
            },
            {
                'strategy': 'alternate_format',
                'suffix': 'HTML text transcript official report accessible',
                'explanation': 'Switch source format when previous reads failed.',
                'priority': 100,
            },
        ),
        'answer_not_ready': (
            {
                'strategy': 'answer_readiness_escalation',
                'suffix': 'authoritative cited evidence complete answer primary sources contradictions resolved',
                'explanation': 'Escalate answer readiness repair with primary evidence and contradiction resolution constraints.',
                'priority': 112,
            },
            {
                'strategy': 'blocker_targeted_repair',
                'suffix': 'missing evidence blockers citation repair official source',
                'explanation': 'Target the answer-readiness blockers directly.',
                'priority': 109,
            },
        ),
        'missing_intent': (
            {
                'strategy': 'intent_retarget',
                'suffix': 'targeted evidence primary source dataset official report',
                'explanation': 'Retarget the missing intent with primary evidence constraints.',
                'priority': 106,
            },
        ),
        'research_quality_gap': (
            {
                'strategy': 'quality_escalation',
                'suffix': 'primary evidence independent verification official source detailed analysis',
                'explanation': 'Escalate general quality repair toward primary and independently verified evidence.',
                'priority': 105,
            },
        ),
    }
    return list(
        options.get(
            gap_code,
            (
                {
                    'strategy': 'generic_escalation',
                    'suffix': 'primary evidence independent sources official report verification',
                    'explanation': 'Escalate unresolved remediation with primary evidence and independent verification terms.',
                    'priority': 104,
                },
            ),
        )
    )


def _finalize_remediation_strategy_learning(by_key: dict[tuple[str, str], dict[str, Any]], *, source: str = 'local') -> dict[str, Any]:
    strategies = []
    for item in by_key.values():
        attempts = int(item.get('attempts') or 0)
        resolved = int(item.get('resolved') or 0)
        completed = attempts - int(item.get('pending') or 0)
        success_rate = round(resolved / completed, 3) if completed > 0 else None
        smoothed = (resolved + 1) / (completed + 2) if completed > 0 else 0.5
        confidence = min(1.0, completed / 5)
        learned_priority_delta = round((smoothed - 0.5) * 32 * confidence)
        strategy = {
            **item,
            'completed_attempts': completed,
            'success_rate': success_rate,
            'smoothed_success_score': round(smoothed, 3),
            'confidence': round(confidence, 3),
            'learned_priority_delta': learned_priority_delta,
        }
        strategies.append(strategy)
    strategies.sort(
        key=lambda item: (
            str(item.get('gap_code') or ''),
            -int(item.get('learned_priority_delta') or 0),
            str(item.get('strategy') or ''),
        )
    )
    return {'source': source, 'strategies': strategies, 'strategy_count': len(strategies)}


def _learning_counts_from_strategies(strategies: list[Any]) -> dict[tuple[str, str], dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for strategy_item in strategies:
        if not isinstance(strategy_item, dict):
            continue
        gap_code = str(strategy_item.get('gap_code') or 'unknown').strip() or 'unknown'
        strategy = str(strategy_item.get('strategy') or 'unknown').strip() or 'unknown'
        item = by_key.setdefault(
            (gap_code, strategy),
            {
                'gap_code': gap_code,
                'strategy': strategy,
                'attempts': 0,
                'resolved': 0,
                'remaining': 0,
                'failed': 0,
                'no_result': 0,
                'pending': 0,
            },
        )
        for key in REMEDIATION_STRATEGY_COUNT_KEYS:
            item[key] += int(strategy_item.get(key) or 0)
    return by_key


def _merge_remediation_strategy_learning(*learning_items: dict[str, Any], source: str = 'combined') -> dict[str, Any]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for learning in learning_items:
        if not isinstance(learning, dict):
            continue
        for key, counts in _learning_counts_from_strategies(list(learning.get('strategies') or [])).items():
            item = merged.setdefault(
                key,
                {
                    'gap_code': counts['gap_code'],
                    'strategy': counts['strategy'],
                    'attempts': 0,
                    'resolved': 0,
                    'remaining': 0,
                    'failed': 0,
                    'no_result': 0,
                    'pending': 0,
                },
            )
            for count_key in REMEDIATION_STRATEGY_COUNT_KEYS:
                item[count_key] += int(counts.get(count_key) or 0)
    return _finalize_remediation_strategy_learning(merged, source=source)


def _remediation_strategy_learning(remediation_outcomes: dict[str, Any]) -> dict[str, Any]:
    outcomes = remediation_outcomes.get('outcomes') if isinstance(remediation_outcomes.get('outcomes'), list) else []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict) or not outcome.get('is_upgrade'):
            continue
        gap_code = str(outcome.get('target_gap') or 'unknown').strip() or 'unknown'
        strategy = str(outcome.get('strategy') or 'unknown').strip() or 'unknown'
        item = by_key.setdefault(
            (gap_code, strategy),
            {
                'gap_code': gap_code,
                'strategy': strategy,
                'attempts': 0,
                'resolved': 0,
                'remaining': 0,
                'failed': 0,
                'no_result': 0,
                'pending': 0,
            },
        )
        status = str(outcome.get('outcome') or 'pending')
        item['attempts'] += 1
        if status in {'resolved', 'remaining', 'failed', 'no_result', 'pending'}:
            item[status] += 1
    return _finalize_remediation_strategy_learning(by_key, source='local')


def _director_root_from_record(director: dict[str, Any]) -> Path | None:
    director_path = str(director.get('director_path') or '').strip()
    if not director_path:
        return None
    try:
        return Path(director_path).expanduser().resolve().parent.parent
    except OSError:
        return None


def _load_shared_remediation_strategy_learning(root: Path | None, *, exclude_director_id: str | None = None) -> dict[str, Any]:
    if root is None:
        return {'source': 'shared', 'strategies': [], 'strategy_count': 0, 'director_count': 0}
    path = root.expanduser().resolve() / REMEDIATION_STRATEGY_LEARNING_FILE
    store = _read_json(path)
    directors = store.get('directors') if isinstance(store.get('directors'), dict) else {}
    learning_items = []
    for director_id, item in directors.items():
        if exclude_director_id and str(director_id) == exclude_director_id:
            continue
        if isinstance(item, dict):
            learning_items.append(item)
    merged = _merge_remediation_strategy_learning(*learning_items, source='shared')
    merged['director_count'] = len(learning_items)
    merged['store_path'] = str(path)
    return merged


def _persist_shared_remediation_strategy_learning(root: Path | None, director_id: str, local_learning: dict[str, Any]) -> dict[str, Any]:
    if root is None or not director_id:
        return {'ok': False, 'message': 'Shared remediation strategy learning store is unavailable.'}
    path = root.expanduser().resolve() / REMEDIATION_STRATEGY_LEARNING_FILE
    store = _read_json(path)
    directors = store.get('directors') if isinstance(store.get('directors'), dict) else {}
    directors[director_id] = {
        'director_id': director_id,
        'updated_at': utc_now(),
        'strategies': list(local_learning.get('strategies') or []),
        'strategy_count': int(local_learning.get('strategy_count') or 0),
    }
    aggregate = _merge_remediation_strategy_learning(*[item for item in directors.values() if isinstance(item, dict)], source='shared')
    payload = {
        'schema_version': 1,
        'updated_at': utc_now(),
        'director_count': len(directors),
        'strategy_count': aggregate.get('strategy_count', 0),
        'aggregate': aggregate,
        'directors': directors,
    }
    _write_json(path, payload)
    return {
        'ok': True,
        'store_path': str(path),
        'director_count': len(directors),
        'strategy_count': aggregate.get('strategy_count', 0),
    }


def _learning_store_from_payload(payload: dict[str, Any], *, source_label: str) -> dict[str, Any]:
    embedded = payload.get('remediation_learning')
    if isinstance(embedded, dict):
        return _learning_store_from_payload(embedded, source_label=source_label)
    directors = payload.get('directors') if isinstance(payload.get('directors'), dict) else {}
    if directors:
        cleaned_directors = {
            str(director_id): item
            for director_id, item in directors.items()
            if str(director_id).strip() and isinstance(item, dict)
        }
        aggregate = _merge_remediation_strategy_learning(*cleaned_directors.values(), source='shared')
        return {
            'ok': True,
            'source_label': source_label,
            'directors': cleaned_directors,
            'director_count': len(cleaned_directors),
            'strategy_count': aggregate.get('strategy_count', 0),
            'aggregate': aggregate,
        }
    aggregate = payload.get('shared_aggregate') if isinstance(payload.get('shared_aggregate'), dict) else {}
    if not aggregate:
        aggregate = payload.get('aggregate') if isinstance(payload.get('aggregate'), dict) else {}
    if not aggregate and isinstance(payload.get('combined_assessment_learning'), dict):
        aggregate = payload['combined_assessment_learning']
    strategies = list(aggregate.get('strategies') or payload.get('strategies') or [])
    if not strategies:
        return {'ok': False, 'message': 'Learning payload does not contain strategies or director records.'}
    synthetic_id = f"imported-{hashlib.sha256(json.dumps(strategies, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:12]}"
    learning = _finalize_remediation_strategy_learning(_learning_counts_from_strategies(strategies), source='imported')
    return {
        'ok': True,
        'source_label': source_label,
        'directors': {
            synthetic_id: {
                'director_id': synthetic_id,
                'updated_at': utc_now(),
                'imported_from': source_label,
                'strategies': learning.get('strategies') or [],
                'strategy_count': learning.get('strategy_count') or 0,
            }
        },
        'director_count': 1,
        'strategy_count': learning.get('strategy_count') or 0,
        'aggregate': learning,
    }


def _read_learning_json_from_tar(path: Path) -> tuple[dict[str, Any], str] | None:
    candidates = {
        'remediation_learning/remediation_strategy_learning.json',
        'remediation_strategy_learning.json',
        'remediation_learning/remediation_learning.json',
        'remediation_learning.json',
    }
    try:
        with tarfile.open(path, 'r:gz') as archive:
            members = sorted(archive.getmembers(), key=lambda item: (item.name not in candidates, item.name))
            for member in members:
                if not member.isfile() or member.name not in candidates:
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                try:
                    value = json.loads(handle.read().decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    return value, f'{path}:{member.name}'
    except (OSError, tarfile.TarError):
        return None
    return None


def _load_learning_import_source(source_path: Path) -> dict[str, Any]:
    candidate = source_path.expanduser()
    if candidate.is_dir():
        candidates = [
            candidate / 'remediation_learning' / REMEDIATION_STRATEGY_LEARNING_FILE,
            candidate / REMEDIATION_STRATEGY_LEARNING_FILE,
            candidate / 'remediation_learning' / 'remediation_learning.json',
            candidate / 'remediation_learning.json',
            candidate / 'runbook' / 'remediation_learning.json',
            candidate / 'runbook.json',
        ]
        for path in candidates:
            if path.exists():
                payload = _read_json(path)
                if payload:
                    parsed = _learning_store_from_payload(payload, source_label=str(path))
                    if parsed.get('ok'):
                        parsed['source_path'] = str(path)
                        return parsed
        return {'ok': False, 'message': f'No remediation learning JSON found in {candidate}.'}
    if candidate.suffixes[-2:] == ['.tar', '.gz'] or candidate.suffix == '.tgz':
        loaded = _read_learning_json_from_tar(candidate)
        if not loaded:
            return {'ok': False, 'message': f'No remediation learning JSON found in archive {candidate}.'}
        payload, label = loaded
        parsed = _learning_store_from_payload(payload, source_label=label)
        parsed['source_path'] = label
        return parsed
    payload = _read_json(candidate)
    if not payload:
        return {'ok': False, 'message': f'Could not read remediation learning JSON: {candidate}'}
    parsed = _learning_store_from_payload(payload, source_label=str(candidate))
    parsed['source_path'] = str(candidate)
    return parsed


def import_remediation_strategy_learning(root: Path, source_path: Path, *, apply: bool = False) -> dict[str, Any]:
    imported = _load_learning_import_source(source_path)
    if not imported.get('ok'):
        return imported
    root = root.expanduser().resolve()
    store_path = root / REMEDIATION_STRATEGY_LEARNING_FILE
    current = _read_json(store_path)
    current_directors = current.get('directors') if isinstance(current.get('directors'), dict) else {}
    imported_directors = imported.get('directors') if isinstance(imported.get('directors'), dict) else {}
    merged_directors = dict(current_directors)
    conflicts = []
    added = []
    replaced = []
    for director_id, learning in imported_directors.items():
        if director_id in merged_directors:
            conflicts.append(director_id)
            replaced.append(director_id)
        else:
            added.append(director_id)
        merged = dict(learning)
        merged['imported_at'] = utc_now()
        merged['import_source_path'] = imported.get('source_path')
        merged_directors[director_id] = merged
    aggregate = _merge_remediation_strategy_learning(*[item for item in merged_directors.values() if isinstance(item, dict)], source='shared')
    payload = {
        'schema_version': 1,
        'updated_at': utc_now(),
        'director_count': len(merged_directors),
        'strategy_count': aggregate.get('strategy_count', 0),
        'aggregate': aggregate,
        'directors': merged_directors,
    }
    if apply:
        _write_json(store_path, payload)
    return {
        'ok': True,
        'dry_run': not apply,
        'source_path': imported.get('source_path'),
        'store_path': str(store_path),
        'imported_director_count': len(imported_directors),
        'added_director_count': len(added),
        'replaced_director_count': len(replaced),
        'conflicts': conflicts,
        'strategy_count': aggregate.get('strategy_count', 0),
        'director_count': len(merged_directors),
        'imported_strategy_count': imported.get('strategy_count', 0),
        'message': 'Preview only. Add apply=true to import remediation learning.' if not apply else 'Remediation strategy learning imported.',
    }


def _strategy_learning_entry(learning: dict[str, Any], gap_code: str, strategy: str) -> dict[str, Any]:
    for item in learning.get('strategies', []) or []:
        if not isinstance(item, dict):
            continue
        if item.get('gap_code') == gap_code and item.get('strategy') == strategy:
            return item
    return {}


def _upgrade_query_for_strategy(base_query: str, outcome: str, option: dict[str, Any]) -> tuple[str, str]:
    cleaned = ' '.join(str(base_query or '').split())
    if not cleaned:
        cleaned = 'research question'
    suffix = str(option.get('suffix') or '').strip()
    reason = str(option.get('explanation') or '').strip()
    if outcome == 'failed':
        suffix = f'{suffix} alternate query terms'
        reason = f'{reason} The previous remediation job failed.'
    elif outcome == 'no_result':
        suffix = f'{suffix} alternative accessible sources'
        reason = f'{reason} The previous remediation job produced no run result.'
    query = f'{cleaned} {suffix}'
    return ' '.join(query.split()), reason


def _director_remediation_strategy_upgrades(
    remediation_outcomes: dict[str, Any],
    *,
    runs_root: Path,
    learning: dict[str, Any],
    limit: int = 8,
) -> list[dict[str, Any]]:
    outcomes = remediation_outcomes.get('outcomes') if isinstance(remediation_outcomes.get('outcomes'), list) else []
    upgrades: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    pending = {
        (
            str(outcome.get('target_gap') or ''),
            str(outcome.get('strategy') or ''),
            str(outcome.get('source_run_id') or '') or None,
        )
        for outcome in outcomes
        if isinstance(outcome, dict) and outcome.get('outcome') == 'pending' and outcome.get('is_upgrade')
    }
    priority_by_outcome = {'remaining': 8, 'failed': 2, 'no_result': 0}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        status = str(outcome.get('outcome') or '')
        if status not in priority_by_outcome:
            continue
        gap_code = str(outcome.get('target_gap') or 'unknown').strip() or 'unknown'
        source_run_id = str(outcome.get('source_run_id') or '').strip() or None
        previous_strategy = str(outcome.get('strategy') or '').strip()
        options = []
        for option in _upgrade_strategy_options(gap_code):
            strategy = str(option.get('strategy') or '').strip()
            if not strategy:
                continue
            if outcome.get('is_upgrade') and strategy == previous_strategy:
                continue
            if (gap_code, strategy, source_run_id) in pending:
                continue
            learned = _strategy_learning_entry(learning, gap_code, strategy)
            learned_delta = int(learned.get('learned_priority_delta') or 0)
            options.append(
                {
                    **option,
                    'learned': learned,
                    'rank_priority': int(option.get('priority') or 0) + int(priority_by_outcome[status]) + learned_delta,
                }
            )
        if not options and previous_strategy:
            for option in _upgrade_strategy_options(gap_code):
                if str(option.get('strategy') or '') == previous_strategy:
                    learned = _strategy_learning_entry(learning, gap_code, previous_strategy)
                    options.append(
                        {
                            **option,
                            'learned': learned,
                            'rank_priority': int(option.get('priority') or 0) - 12 + int(learned.get('learned_priority_delta') or 0),
                        }
                    )
                    break
        options.sort(key=lambda item: (int(item.get('rank_priority') or 0), str(item.get('strategy') or '')), reverse=True)
        if not options:
            continue
        option = options[0]
        strategy = str(option.get('strategy') or 'generic_escalation')
        dedupe_key = (gap_code, strategy, source_run_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        query, reason = _upgrade_query_for_strategy(_upgrade_base_query(outcome, runs_root), status, option)
        learned = option.get('learned') if isinstance(option.get('learned'), dict) else {}
        upgrades.append(
            {
                'query': query,
                'reason': f'remediation_upgrade:{gap_code}',
                'run_id': source_run_id,
                'source_job_id': outcome.get('job_id'),
                'target_gap': gap_code,
                'previous_outcome': status,
                'previous_strategy': previous_strategy or None,
                'strategy': strategy,
                'priority': int(option.get('rank_priority') or 0),
                'base_priority': int(option.get('priority') or 0),
                'learned_priority_delta': int(learned.get('learned_priority_delta') or 0),
                'learned_success_rate': learned.get('success_rate'),
                'explanation': reason,
            }
        )
        if len(upgrades) >= limit:
            break
    return upgrades


def assess_research_director(
    director: dict[str, Any],
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
) -> dict[str, Any]:
    loaded_campaign = load_research_campaign(campaign_root, str(director.get('campaign_id') or ''))
    if not loaded_campaign.get('ok'):
        return {'ok': False, 'message': loaded_campaign.get('message'), 'director': _director_summary(director)}
    campaign = summarize_campaign(loaded_campaign['campaign'], jobs_root=jobs_root, runs_root=runs_root)
    target_score = int(director.get('quality_target_score') or _quality_target_score(str(director.get('quality_target') or 'moderate')))
    run_reviews = []
    run_payloads = []
    follow_up_candidates = []
    for run_id in campaign.get('run_ids') or []:
        loaded = load_research_run(str(run_id), root=runs_root)
        if not loaded.get('ok'):
            continue
        payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
        run_payloads.append(payload)
        metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
        quality = _run_quality(payload)
        recommended = [str(item) for item in payload.get('recommended_next_searches', []) or [] if str(item).strip()]
        remediation_plan = (
            payload.get('remediation_plan')
            if isinstance(payload.get('remediation_plan'), dict)
            else build_research_remediation_plan(payload)
        )
        gaps = []
        if quality['score'] < target_score:
            gaps.append('below_quality_target')
        source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
        if int(source_quality.get('primary_source_count') or 0) == 0:
            gaps.append('missing_primary_sources')
        if _contradiction_issue_count(payload):
            gaps.append('contradiction_needs_review')
        answer_readiness = payload.get('answer_readiness') if isinstance(payload.get('answer_readiness'), dict) else {}
        if answer_readiness and not answer_readiness.get('ok'):
            gaps.append('answer_not_ready')
        review = {
            'run_id': run_id,
            'query': metadata.get('query'),
            'status': metadata.get('status'),
            'quality': quality,
            'gaps': gaps,
            'recommended_next_searches': recommended[:5],
            'remediation_plan': {
                'gap_count': remediation_plan.get('gap_count', 0),
                'action_count': remediation_plan.get('action_count', 0),
                'gaps': list(remediation_plan.get('gaps', []) or [])[:5],
                'actions': list(remediation_plan.get('actions', []) or [])[:5],
            },
        }
        run_reviews.append(review)
        for action in remediation_plan.get('actions', []) or []:
            if isinstance(action, dict) and action.get('query'):
                follow_up_candidates.append(
                    {
                        'query': str(action['query']),
                        'reason': f"evidence_remediation:{action.get('gap_code') or 'unknown'}",
                        'run_id': run_id,
                        'priority': action.get('priority'),
                    }
                )
        for query in recommended:
            follow_up_candidates.append({'query': query, 'reason': 'run_recommended_next_search', 'run_id': run_id})
        if 'missing_primary_sources' in gaps:
            follow_up_candidates.append({'query': f"Find primary or official sources for: {metadata.get('query')}", 'reason': 'missing_primary_sources', 'run_id': run_id})
        if 'contradiction_needs_review' in gaps:
            follow_up_candidates.append({'query': f"Resolve contradictions and verify disputed claims for: {metadata.get('query')}", 'reason': 'contradiction_needs_review', 'run_id': run_id})
        if 'answer_not_ready' in gaps:
            issues = list(answer_readiness.get('blockers', []) or []) + list(answer_readiness.get('warnings', []) or [])
            issue_text = ' '.join(str(issue) for issue in issues[:2] if str(issue).strip())
            if issue_text:
                query = f"Repair answer readiness for: {metadata.get('query')} {issue_text[:160]}"
            else:
                query = f"Repair answer readiness with stronger cited evidence for: {metadata.get('query')}"
            follow_up_candidates.append({'query': query, 'reason': 'answer_not_ready', 'run_id': run_id})
    step_counts = campaign.get('step_status_counts') if isinstance(campaign.get('step_status_counts'), dict) else {}
    incomplete_steps = [
        step
        for step in campaign.get('steps') or []
        if str(step.get('status') or '') not in {'completed'}
    ]
    for step in incomplete_steps:
        if str(step.get('status') or '') in {'failed', 'cancelled'}:
            follow_up_candidates.append({'query': str(step.get('question') or ''), 'reason': f"step_{step.get('status')}", 'step_id': step.get('step_id')})
    remediation_outcomes = _director_remediation_outcomes(director, jobs_root=jobs_root, runs_root=runs_root)
    local_strategy_learning = _remediation_strategy_learning(remediation_outcomes)
    shared_strategy_learning = _load_shared_remediation_strategy_learning(
        _director_root_from_record(director),
        exclude_director_id=str(director.get('director_id') or ''),
    )
    remediation_strategy_learning = _merge_remediation_strategy_learning(
        local_strategy_learning,
        shared_strategy_learning,
        source='combined',
    )
    remediation_strategy_learning['local'] = local_strategy_learning
    remediation_strategy_learning['shared'] = shared_strategy_learning
    remediation_strategy_upgrades = _director_remediation_strategy_upgrades(
        remediation_outcomes,
        runs_root=runs_root,
        learning=remediation_strategy_learning,
    )
    follow_up_candidates = remediation_strategy_upgrades + follow_up_candidates
    unique_candidates = []
    seen = set()
    for candidate in follow_up_candidates:
        query = ' '.join(str(candidate.get('query') or '').split())
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        item = dict(candidate)
        item['query'] = query
        unique_candidates.append(item)
    unique_candidates.sort(key=lambda item: int(item.get('priority') or 0), reverse=True)
    initial = int(director.get('initial_job_count') or 0)
    followups = len(director.get('followup_job_ids') or [])
    remaining_budget = max(0, int(director.get('budget_jobs') or initial) - initial - followups)
    bounded_candidates = unique_candidates[:remaining_budget or 0]
    quality_gate = _build_quality_gate(
        campaign=campaign,
        run_reviews=run_reviews,
        run_payloads=run_payloads,
        target_score=target_score,
        incomplete_steps=incomplete_steps,
        follow_up_candidates=bounded_candidates,
        remaining_budget=remaining_budget,
    )
    ready_to_synthesize = quality_gate.get('recommended_action') == 'synthesize'
    return {
        'ok': True,
        'director': _director_summary(director),
        'campaign': campaign,
        'run_reviews': run_reviews,
        'quality_target_score': target_score,
        'step_status_counts': step_counts,
        'incomplete_step_count': len(incomplete_steps),
        'follow_up_candidates': bounded_candidates,
        'remediation_outcomes': remediation_outcomes,
        'remediation_strategy_learning': remediation_strategy_learning,
        'remediation_strategy_upgrades': remediation_strategy_upgrades,
        'remaining_followup_budget': remaining_budget,
        'ready_to_synthesize': ready_to_synthesize,
        'quality_gate': quality_gate,
    }


def advance_research_director(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    synthesis_root: Path,
    apply: bool = False,
    synthesize: bool = False,
    local_synthesis: bool = False,
    max_followups: int = 3,
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    director = dict(loaded['director'])
    assessment = assess_research_director(director, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
    if not assessment.get('ok'):
        return assessment
    actions = []
    created_followups = []
    gate = assessment.get('quality_gate') if isinstance(assessment.get('quality_gate'), dict) else {}
    followups = assessment.get('follow_up_candidates') or []
    objective_memory = director.get('objective_memory') if isinstance(director.get('objective_memory'), dict) else {}
    max_followups = max(0, min(max_followups, int(assessment.get('remaining_followup_budget') or 0)))
    if gate.get('recommended_action') == 'stop_budget_exhausted':
        actions.append('quality_gate_stop_budget_exhausted')
        if apply:
            director['status'] = 'stopped_budget_exhausted'
    if followups and max_followups and gate.get('recommended_action') == 'continue':
        if apply:
            for candidate in followups[:max_followups]:
                created = create_research_job(
                    jobs_root,
                    request=_request_with_memory_hints(str(candidate['query']), objective_memory),
                    profile=str(director.get('profile') or 'careful'),
                    priority=int(director.get('priority') or 0),
                    tags=_director_followup_tags(director, director_id, candidate),
                )
                created_followups.append(created)
                if created.get('ok'):
                    director.setdefault('followup_job_ids', []).append(created['job']['job_id'])
            actions.append('created_followup_jobs')
        else:
            actions.append('preview_followup_jobs')
    synthesis_result = None
    should_synthesize = synthesize or gate.get('recommended_action') == 'synthesize'
    if should_synthesize:
        if apply:
            synthesis_payload = build_campaign_synthesis(
                str(director.get('campaign_id')),
                campaign_root=campaign_root,
                jobs_root=jobs_root,
                runs_root=runs_root,
            )
            if synthesis_payload.get('ok') and local_synthesis:
                synthesis_payload = asyncio.run(apply_campaign_narrative_synthesis(synthesis_payload, enabled=True))
            synthesis_result = write_campaign_synthesis_bundle(
                str(director.get('campaign_id')),
                campaign_root=campaign_root,
                jobs_root=jobs_root,
                runs_root=runs_root,
                output_dir=synthesis_root,
                synthesis=synthesis_payload,
            )
            director['synthesis'] = {
                'ok': synthesis_result.get('ok'),
                'bundle_dir': synthesis_result.get('bundle_dir'),
                'run_count': synthesis_result.get('run_count'),
                'source_count': synthesis_result.get('source_count'),
                'claim_count': synthesis_result.get('claim_count'),
                'local_synthesis_requested': local_synthesis,
            }
            director['status'] = 'synthesized' if synthesis_result.get('ok') else 'synthesis_failed'
            actions.append('wrote_synthesis')
        else:
            actions.append('preview_synthesis')
    if apply and actions:
        director['updated_at'] = utc_now()
        director.setdefault('events', []).append({'timestamp': director['updated_at'], 'event': 'advanced', 'actions': actions})
        _write_json(Path(str(director['director_path'])), director)
    updated_assessment = assess_research_director(director, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
    shared_learning_update = None
    if apply:
        strategy_learning = updated_assessment.get('remediation_strategy_learning') if isinstance(updated_assessment.get('remediation_strategy_learning'), dict) else {}
        local_learning = strategy_learning.get('local') if isinstance(strategy_learning.get('local'), dict) else strategy_learning
        shared_learning_update = _persist_shared_remediation_strategy_learning(
            _director_root_from_record(director),
            director_id,
            local_learning,
        )
    return {
        'ok': True,
        'dry_run': not apply,
        'actions': actions or ['assessed'],
        'director': _director_summary(director),
        'assessment': updated_assessment,
        'shared_remediation_strategy_learning': shared_learning_update,
        'planned_followups': followups[:max_followups],
        'created_followups': created_followups,
        'synthesis': synthesis_result,
        'message': 'Preview only. Add apply=true to create follow-up jobs or synthesis bundles.' if not apply else 'Research director advanced.',
    }


def run_research_director_wave(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    synthesis_root: Path,
    worker_state_dir: Path,
    apply: bool = False,
    start_worker_enabled: bool = False,
    max_cycles: int = 3,
    max_followups: int = 3,
    local_synthesis: bool = False,
    worker_id: str = 'research-director-wave',
    lease_seconds: int = 3600,
    poll_seconds: float = 30,
    idle_exit_seconds: float = 0,
    tmux: bool = False,
    session: str = 'lmstudio-research-worker',
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    director = dict(loaded['director'])
    max_cycles = max(1, min(50, int(max_cycles)))
    wave_id = f'{utc_now().replace(":", "").replace("-", "")}-{uuid.uuid4().hex[:8]}'.lower()
    wave_dir = _safe_director_dir(root, director_id) / 'waves' / wave_id
    worker_status = status_worker(state_dir=worker_state_dir)
    worker_start = None
    if start_worker_enabled:
        if apply:
            worker_start = start_worker(
                jobs_root=jobs_root,
                state_dir=worker_state_dir,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                max_jobs=0,
                poll_seconds=poll_seconds,
                idle_exit_seconds=idle_exit_seconds,
                watch=True,
                tmux=tmux,
                session=session,
                dry_run=False,
            )
            worker_status = status_worker(state_dir=worker_state_dir)
        else:
            worker_start = {
                'ok': True,
                'started': False,
                'dry_run': True,
                'state': {
                    'worker_id': worker_id,
                    'jobs_root': str(jobs_root),
                    'state_dir': str(worker_state_dir),
                    'watch': True,
                    'tmux': tmux,
                    'session': session if tmux else None,
                },
            }
    cycles = []
    stop_reason = 'max_cycles'
    for cycle_number in range(1, max_cycles + 1):
        before = assess_research_director(director, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
        gate = before.get('quality_gate') if isinstance(before.get('quality_gate'), dict) else {}
        recommended = str(gate.get('recommended_action') or 'unknown')
        if recommended in {'wait'}:
            stop_reason = 'waiting_for_worker'
            cycles.append(
                {
                    'cycle': cycle_number,
                    'action': 'wait',
                    'quality_gate': gate,
                    'assessment': before,
                }
            )
            break
        advanced = advance_research_director(
            root,
            director_id,
            campaign_root=campaign_root,
            jobs_root=jobs_root,
            runs_root=runs_root,
            synthesis_root=synthesis_root,
            apply=apply,
            synthesize=recommended == 'synthesize',
            local_synthesis=local_synthesis,
            max_followups=max_followups,
        )
        loaded_after = load_research_director(root, director_id)
        if loaded_after.get('ok'):
            director = dict(loaded_after['director'])
        after = advanced.get('assessment') if isinstance(advanced.get('assessment'), dict) else before
        cycles.append(
            {
                'cycle': cycle_number,
                'action': recommended,
                'quality_gate': gate,
                'advance': advanced,
                'assessment': after,
            }
        )
        if recommended in {'synthesize', 'stop_budget_exhausted'}:
            stop_reason = recommended
            break
    payload = {
        'ok': True,
        'dry_run': not apply,
        'wave_id': wave_id,
        'director_id': director_id,
        'wave_dir': str(wave_dir),
        'worker_status': worker_status,
        'worker_start': worker_start,
        'cycle_count': len(cycles),
        'stop_reason': stop_reason,
        'cycles': cycles,
        'created_at': utc_now(),
    }
    if apply:
        _write_json(wave_dir / 'wave.json', payload)
    return payload


def director_autopilot_markdown(autopilot: dict[str, Any]) -> str:
    director = autopilot.get('director') if isinstance(autopilot.get('director'), dict) else {}
    lines = [
        f"# Research Director Autopilot: {_fmt(autopilot.get('autopilot_id'))}",
        '',
        f"- Director: {_fmt(autopilot.get('director_id'))}",
        f"- Objective: {_fmt(director.get('objective'))}",
        f"- Dry run: {_fmt(autopilot.get('dry_run'))}",
        f"- Iterations: {_fmt(autopilot.get('iteration_count'))}",
        f"- Stop reason: {_fmt(autopilot.get('stop_reason'))}",
        f"- Worker mode: {_fmt(autopilot.get('worker_mode'))}",
        f"- Autopilot JSON: {_fmt(autopilot.get('autopilot_path'))}",
        '',
        '## Iterations',
        '',
        '| Iteration | Gate Action | Recovery | Wave Stop | Worker Jobs | Followups | Artifacts |',
        '| ---: | --- | --- | --- | ---: | ---: | --- |',
    ]
    for iteration in autopilot.get('iterations', []) or []:
        if not isinstance(iteration, dict):
            continue
        gate = iteration.get('quality_gate') if isinstance(iteration.get('quality_gate'), dict) else {}
        recovery = iteration.get('recovery') if isinstance(iteration.get('recovery'), dict) else {}
        recovery_counts = recovery.get('issue_counts') if isinstance(recovery.get('issue_counts'), dict) else {}
        worker = iteration.get('worker_run') if isinstance(iteration.get('worker_run'), dict) else {}
        wave = iteration.get('wave') if isinstance(iteration.get('wave'), dict) else {}
        created_followups = 0
        for cycle in wave.get('cycles', []) or []:
            if not isinstance(cycle, dict):
                continue
            advance = cycle.get('advance') if isinstance(cycle.get('advance'), dict) else {}
            created_followups += len(advance.get('created_followups') or [])
        artifacts = []
        if iteration.get('dashboard_path'):
            artifacts.append('dashboard')
        if iteration.get('runbook_path'):
            artifacts.append('runbook')
        if iteration.get('synthesis_bundle_dir'):
            artifacts.append('synthesis')
        lines.append(
            f"| {_fmt(iteration.get('iteration'))} | {_fmt(gate.get('recommended_action'))} | "
            f"{_fmt(recovery.get('policy', {}).get('name') if isinstance(recovery.get('policy'), dict) else None)} "
            f"stuck={_fmt(recovery_counts.get('stuck_jobs', 0))} cancelled={_fmt(recovery_counts.get('cancelled_jobs', 0))} | "
            f"{_fmt(wave.get('stop_reason'))} | {_fmt(worker.get('worked_count', 0))} | "
            f"{_fmt(created_followups)} | {', '.join(artifacts) or 'none'} |"
        )
    lines.extend(['', '## Stop Detail', ''])
    lines.append(f"- {_fmt(autopilot.get('message'))}")
    lines.append('')
    return '\n'.join(lines)


def run_research_director_autopilot(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    synthesis_root: Path,
    worker_state_dir: Path,
    apply: bool = False,
    max_iterations: int = 5,
    max_cycles_per_iteration: int = 2,
    max_followups: int = 3,
    start_worker_enabled: bool = False,
    run_worker_enabled: bool = False,
    worker_jobs_per_iteration: int = 1,
    worker_id: str = 'research-director-autopilot',
    local_synthesis: bool = False,
    recovery_policy: str = 'none',
    auto_recover: bool = False,
    write_dashboard: bool = True,
    write_runbook_on_stop: bool = True,
    tmux: bool = False,
    session: str = 'lmstudio-research-worker',
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    director = dict(loaded['director'])
    max_iterations = max(1, min(50, int(max_iterations)))
    max_cycles_per_iteration = max(1, min(20, int(max_cycles_per_iteration)))
    max_followups = max(0, min(20, int(max_followups)))
    worker_jobs_per_iteration = max(0, min(25, int(worker_jobs_per_iteration)))
    autopilot_id = f'{utc_now().replace(":", "").replace("-", "")}-{uuid.uuid4().hex[:8]}'.lower()
    autopilot_dir = _safe_director_dir(root, director_id) / 'autopilots' / autopilot_id
    autopilot_path = autopilot_dir / 'autopilot.json'
    report_path = autopilot_dir / 'autopilot.md'
    iterations: list[dict[str, Any]] = []
    stop_reason = 'max_iterations'
    message = 'Autopilot reached the configured iteration limit.'
    worker_start_once = bool(start_worker_enabled)
    normalized_recovery_policy = str(recovery_policy or 'none').strip().lower().replace('-', '_')
    recover_each_iteration = bool(auto_recover or normalized_recovery_policy not in {'', 'none', 'off', 'false'})

    for iteration_number in range(1, max_iterations + 1):
        recovery = None
        if recover_each_iteration:
            policy_name = normalized_recovery_policy if normalized_recovery_policy not in {'', 'none', 'off', 'false'} else 'balanced'
            recovery_policy_config = _recovery_policy(policy_name)
            recovery = recover_research_director(
                root,
                director_id,
                campaign_root=campaign_root,
                jobs_root=jobs_root,
                runs_root=runs_root,
                worker_state_dir=worker_state_dir,
                apply=apply,
                stale_hours=int(recovery_policy_config['stale_hours']),
                policy=policy_name,
                cancel_stuck_jobs=bool(recovery_policy_config.get('cancel_stuck_jobs')),
                review_waves=bool(recovery_policy_config.get('review_waves')),
                start_worker_enabled=False,
                resume_checkpoints=bool(recovery_policy_config.get('allow_checkpoint_resume')),
                tmux=tmux,
            )
            loaded_recovered = load_research_director(root, director_id)
            if loaded_recovered.get('ok'):
                director = dict(loaded_recovered['director'])

        worker_run = None
        if run_worker_enabled and worker_jobs_per_iteration:
            if apply:
                from scripts.research_job_worker import run_worker

                worker_run = asyncio.run(
                    run_worker(
                        jobs_root,
                        worker_id=f'{worker_id}-{iteration_number}',
                        lease_seconds=3600,
                        max_jobs=worker_jobs_per_iteration,
                    )
                )
            else:
                worker_run = {
                    'ok': True,
                    'dry_run': True,
                    'worker_id': f'{worker_id}-{iteration_number}',
                    'worked_count': 0,
                    'planned_max_jobs': worker_jobs_per_iteration,
                }

        before = assess_research_director(director, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
        gate = before.get('quality_gate') if isinstance(before.get('quality_gate'), dict) else {}
        recommended = str(gate.get('recommended_action') or 'unknown')
        wave = run_research_director_wave(
            root,
            director_id,
            campaign_root=campaign_root,
            jobs_root=jobs_root,
            runs_root=runs_root,
            synthesis_root=synthesis_root,
            worker_state_dir=worker_state_dir,
            apply=apply,
            start_worker_enabled=worker_start_once,
            max_cycles=max_cycles_per_iteration,
            max_followups=max_followups,
            local_synthesis=local_synthesis,
            worker_id=worker_id,
            tmux=tmux,
            session=session,
        )
        worker_start_once = False
        loaded_after = load_research_director(root, director_id)
        if loaded_after.get('ok'):
            director = dict(loaded_after['director'])

        dashboard = None
        if write_dashboard:
            dashboard = build_research_director_dashboard(
                root,
                director_id,
                campaign_root=campaign_root,
                jobs_root=jobs_root,
                runs_root=runs_root,
                apply=apply,
            )
        runbook = None
        synthesis_bundle_dir = None
        created_followup_count = 0
        for cycle in wave.get('cycles', []) or []:
            if not isinstance(cycle, dict):
                continue
            advance = cycle.get('advance') if isinstance(cycle.get('advance'), dict) else {}
            created_followup_count += len(advance.get('created_followups') or [])
            synthesis = advance.get('synthesis') if isinstance(advance.get('synthesis'), dict) else {}
            if synthesis.get('bundle_dir'):
                synthesis_bundle_dir = synthesis.get('bundle_dir')

        iteration_record = {
            'iteration': iteration_number,
            'started_at': utc_now(),
            'quality_gate': gate,
            'recommended_action': recommended,
            'recovery': recovery,
            'worker_run': worker_run,
            'wave': wave,
            'dashboard_path': dashboard.get('dashboard_path') if isinstance(dashboard, dict) else None,
            'runbook_path': None,
            'synthesis_bundle_dir': synthesis_bundle_dir,
        }
        iterations.append(iteration_record)

        wave_stop = str(wave.get('stop_reason') or '')
        if wave_stop in {'synthesize', 'stop_budget_exhausted'}:
            stop_reason = wave_stop
            message = f'Autopilot stopped because the director wave reached {wave_stop}.'
            break
        if wave_stop == 'waiting_for_worker':
            if run_worker_enabled and apply:
                continue
            stop_reason = 'waiting_for_worker'
            message = 'Autopilot queued or observed work and is waiting for the research worker to finish jobs.'
            break
        if created_followup_count and not run_worker_enabled:
            stop_reason = 'waiting_for_worker'
            message = 'Autopilot queued follow-up jobs and is waiting for an external research worker to finish them.'
            break
        if recommended == 'stop_budget_exhausted':
            stop_reason = 'stop_budget_exhausted'
            message = 'Autopilot stopped because the director follow-up budget is exhausted.'
            break
        if recommended == 'synthesize':
            stop_reason = 'synthesize'
            message = 'Autopilot stopped after synthesis.'
            break

    if write_runbook_on_stop:
        final_runbook = build_director_runbook(
            root,
            director_id,
            campaign_root=campaign_root,
            jobs_root=jobs_root,
            runs_root=runs_root,
            apply=apply,
        )
        if iterations:
            iterations[-1]['runbook_path'] = final_runbook.get('runbook_path')

    payload = {
        'ok': True,
        'dry_run': not apply,
        'autopilot_id': autopilot_id,
        'director_id': director_id,
        'director': _director_summary(director),
        'worker_mode': 'in_process' if run_worker_enabled else ('detached' if start_worker_enabled else 'observe_only'),
        'recovery_policy': normalized_recovery_policy,
        'auto_recover': recover_each_iteration,
        'max_iterations': max_iterations,
        'max_cycles_per_iteration': max_cycles_per_iteration,
        'max_followups': max_followups,
        'iteration_count': len(iterations),
        'stop_reason': stop_reason,
        'iterations': iterations,
        'autopilot_path': str(autopilot_path),
        'report_path': str(report_path),
        'created_at': utc_now(),
        'message': message,
    }
    payload['markdown'] = director_autopilot_markdown(payload)
    if apply:
        autopilot_dir.mkdir(parents=True, exist_ok=True)
        _write_json(autopilot_path, {key: value for key, value in payload.items() if key != 'markdown'})
        report_path.write_text(str(payload['markdown']), encoding='utf-8')
        loaded_final = load_research_director(root, director_id)
        if loaded_final.get('ok'):
            updated = dict(loaded_final['director'])
            updated['updated_at'] = utc_now()
            updated.setdefault('events', []).append(
                {
                    'timestamp': updated['updated_at'],
                    'event': 'autopilot',
                    'autopilot_id': autopilot_id,
                    'stop_reason': stop_reason,
                    'iteration_count': len(iterations),
                    'autopilot_path': str(autopilot_path),
                }
            )
            _write_json(Path(str(updated['director_path'])), updated)
            payload['director'] = _director_summary(updated)
    return payload


def _autopilot_summary(autopilot: dict[str, Any], path: Path) -> dict[str, Any]:
    iterations = autopilot.get('iterations') if isinstance(autopilot.get('iterations'), list) else []
    latest = iterations[-1] if iterations and isinstance(iterations[-1], dict) else {}
    gate = latest.get('quality_gate') if isinstance(latest.get('quality_gate'), dict) else {}
    recovery = latest.get('recovery') if isinstance(latest.get('recovery'), dict) else {}
    recovery_counts = recovery.get('issue_counts') if isinstance(recovery.get('issue_counts'), dict) else {}
    return {
        'autopilot_id': autopilot.get('autopilot_id') or path.parent.name,
        'director_id': autopilot.get('director_id'),
        'stop_reason': autopilot.get('stop_reason'),
        'iteration_count': autopilot.get('iteration_count') or len(iterations),
        'worker_mode': autopilot.get('worker_mode'),
        'auto_recover': bool(autopilot.get('auto_recover')),
        'recovery_policy': autopilot.get('recovery_policy'),
        'latest_gate_action': gate.get('recommended_action'),
        'latest_recovery_issue_counts': recovery_counts,
        'created_at': autopilot.get('created_at'),
        'message': autopilot.get('message'),
        'autopilot_path': str(path),
        'report_path': str(path.with_suffix('.md')),
    }


def list_director_autopilots(root: Path, director_id: str, *, limit: int = 20) -> dict[str, Any]:
    try:
        director_dir = _safe_director_dir(root, director_id)
    except ValueError as exc:
        return {'ok': False, 'message': str(exc), 'director_id': director_id}
    autopilots = []
    for path in (director_dir / 'autopilots').glob('*/autopilot.json'):
        payload = _read_json(path)
        if payload:
            autopilots.append(_autopilot_summary(payload, path))
    autopilots.sort(key=lambda item: str(item.get('created_at') or ''), reverse=True)
    limit = max(1, min(100, int(limit)))
    return {
        'ok': True,
        'director_id': director_id,
        'autopilots': autopilots[:limit],
        'count': len(autopilots[:limit]),
        'total_count': len(autopilots),
    }


def load_director_autopilot(root: Path, director_id: str, autopilot_id: str | None = None) -> dict[str, Any]:
    listed = list_director_autopilots(root, director_id, limit=100)
    if not listed.get('ok'):
        return listed
    candidates = listed.get('autopilots') if isinstance(listed.get('autopilots'), list) else []
    selected = None
    if autopilot_id:
        for item in candidates:
            if str(item.get('autopilot_id') or '') == str(autopilot_id):
                selected = item
                break
    elif candidates:
        selected = candidates[0]
    if not selected:
        return {'ok': False, 'director_id': director_id, 'autopilot_id': autopilot_id, 'message': 'Research director autopilot not found.'}
    path = Path(str(selected.get('autopilot_path') or ''))
    payload = _read_json(path)
    if not payload:
        return {'ok': False, 'director_id': director_id, 'autopilot_id': selected.get('autopilot_id'), 'message': 'Could not read autopilot artifact.'}
    payload['summary'] = selected
    payload['autopilot_path'] = str(path)
    payload['report_path'] = str(path.with_suffix('.md'))
    return {'ok': True, 'director_id': director_id, 'autopilot': payload, 'summary': selected}


def list_director_waves(root: Path, director_id: str, *, limit: int = 20) -> dict[str, Any]:
    try:
        director_dir = _safe_director_dir(root, director_id)
    except ValueError as exc:
        return {'ok': False, 'message': str(exc), 'director_id': director_id}
    waves = []
    for path in (director_dir / 'waves').glob('*/wave.json'):
        wave = _read_json(path)
        if wave:
            wave['wave_path'] = str(path)
            waves.append(wave)
    waves.sort(key=lambda item: str(item.get('created_at') or ''), reverse=True)
    limit = max(1, min(100, int(limit)))
    return {'ok': True, 'director_id': director_id, 'waves': waves[:limit], 'count': len(waves[:limit]), 'total_count': len(waves)}


def _fmt(value: Any) -> str:
    if value is None or value == '':
        return 'n/a'
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    return str(value)


def director_dashboard_markdown(dashboard: dict[str, Any]) -> str:
    director = dashboard.get('director') if isinstance(dashboard.get('director'), dict) else {}
    assessment = dashboard.get('assessment') if isinstance(dashboard.get('assessment'), dict) else {}
    gate = assessment.get('quality_gate') if isinstance(assessment.get('quality_gate'), dict) else {}
    campaign = assessment.get('campaign') if isinstance(assessment.get('campaign'), dict) else {}
    graph_summary = dashboard.get('graph_summary') if isinstance(dashboard.get('graph_summary'), dict) else {}
    lines = [
        f"# Research Director: {_fmt(director.get('director_id'))}",
        '',
        f"- Objective: {_fmt(director.get('objective'))}",
        f"- Status: {_fmt(director.get('status'))}",
        f"- Campaign: {_fmt(director.get('campaign_id'))}",
        f"- Profile/depth: {_fmt(director.get('profile'))} / {_fmt(director.get('depth'))}",
        f"- Budget jobs: {_fmt(director.get('budget_jobs'))}",
        f"- Follow-up jobs: {_fmt(director.get('followup_job_count'))}",
        f"- Current gate action: {_fmt(gate.get('recommended_action'))}",
        f"- Gate pass: {_fmt(gate.get('ok'))}",
        f"- Average/min score: {_fmt(gate.get('average_score'))} / {_fmt(gate.get('min_score'))}",
        f"- Remaining follow-up budget: {_fmt(gate.get('remaining_followup_budget'))}",
        f"- Dashboard JSON: {_fmt(dashboard.get('dashboard_path'))}",
        '',
        '## Gate Failures',
        '',
    ]
    failures = gate.get('failures') if isinstance(gate.get('failures'), list) else []
    if failures:
        lines.extend(f'- {failure}' for failure in failures)
    else:
        lines.append('- none')
    outcomes = assessment.get('remediation_outcomes') if isinstance(assessment.get('remediation_outcomes'), dict) else {}
    outcome_counts = outcomes.get('counts') if isinstance(outcomes.get('counts'), dict) else {}
    lines.extend(
        [
            '',
            '## Remediation Outcomes',
            '',
            f"- Pending: {_fmt(outcome_counts.get('pending', 0))}",
            f"- Resolved: {_fmt(outcome_counts.get('resolved', 0))}",
            f"- Remaining: {_fmt(outcome_counts.get('remaining', 0))}",
            f"- Failed/no result: {_fmt(int(outcome_counts.get('failed', 0) or 0) + int(outcome_counts.get('no_result', 0) or 0))}",
        ]
    )
    learning = assessment.get('remediation_strategy_learning') if isinstance(assessment.get('remediation_strategy_learning'), dict) else {}
    learned_strategies = learning.get('strategies') if isinstance(learning.get('strategies'), list) else []
    local_learning = learning.get('local') if isinstance(learning.get('local'), dict) else {}
    shared_learning = learning.get('shared') if isinstance(learning.get('shared'), dict) else {}
    lines.extend(['', '## Remediation Strategy Learning', ''])
    lines.append(
        f"- Scope: {_fmt(learning.get('source'))}; "
        f"local strategies={_fmt(local_learning.get('strategy_count', 0))}; "
        f"shared strategies={_fmt(shared_learning.get('strategy_count', 0))} "
        f"from {_fmt(shared_learning.get('director_count', 0))} director(s)"
    )
    if learned_strategies:
        for item in learned_strategies[:8]:
            lines.append(
                f"- {item.get('gap_code')} / {item.get('strategy')}: "
                f"success={_fmt(item.get('success_rate'))}, attempts={_fmt(item.get('attempts'))}, "
                f"delta={_fmt(item.get('learned_priority_delta'))}"
            )
    else:
        lines.append('- none')
    upgrades = assessment.get('remediation_strategy_upgrades') if isinstance(assessment.get('remediation_strategy_upgrades'), list) else []
    lines.extend(['', '## Remediation Strategy Upgrades', ''])
    if upgrades:
        for item in upgrades[:8]:
            lines.append(
                f"- {item.get('target_gap')} after {item.get('previous_outcome')}: "
                f"{item.get('strategy')} - {item.get('query')}"
            )
    else:
        lines.append('- none')
    lines.extend(['', '## Campaign Steps', '', '| Step | Status | Runs | Question |', '| --- | --- | --- | --- |'])
    for step in campaign.get('steps') or []:
        if not isinstance(step, dict):
            continue
        runs = ', '.join(str(item) for item in step.get('run_ids', []) or [])
        question = str(step.get('question') or '').replace('|', '\\|')
        lines.append(f"| {_fmt(step.get('step_id'))} | {_fmt(step.get('status'))} | {runs or 'none'} | {question} |")
    lines.extend(['', '## Run Reviews', '', '| Run | Score | Gaps | Next Searches |', '| --- | ---: | --- | --- |'])
    for review in assessment.get('run_reviews') or []:
        if not isinstance(review, dict):
            continue
        quality = review.get('quality') if isinstance(review.get('quality'), dict) else {}
        gaps = ', '.join(str(item) for item in review.get('gaps', []) or []) or 'none'
        searches = '; '.join(str(item) for item in review.get('recommended_next_searches', []) or []) or 'none'
        lines.append(f"| {_fmt(review.get('run_id'))} | {_fmt(quality.get('score'))} | {gaps} | {searches.replace('|', '\\|')} |")
    lines.extend(['', '## Follow-Up Candidates', ''])
    candidates = assessment.get('follow_up_candidates') if isinstance(assessment.get('follow_up_candidates'), list) else []
    if candidates:
        for item in candidates[:20]:
            lines.append(f"- {item.get('reason')}: {item.get('query')}")
    else:
        lines.append('- none')
    lines.extend(['', '## Graph Insights', ''])
    if graph_summary:
        lines.append(f"- Evidence graph: {_fmt(graph_summary.get('graph_path'))}")
        lines.append(f"- Repeated source domains: {_fmt(len(graph_summary.get('repeated_source_domains') or []))}")
        lines.append(f"- Weak evidence claims: {_fmt(len(graph_summary.get('weak_evidence_claims') or []))}")
        lines.append(f"- Unresolved contradiction chains: {_fmt(len(graph_summary.get('unresolved_contradiction_chains') or []))}")
        central_claims = graph_summary.get('central_claims') if isinstance(graph_summary.get('central_claims'), list) else []
        lines.extend(['', '### Central Claims', ''])
        if central_claims:
            for item in central_claims[:5]:
                lines.append(f"- support={_fmt(item.get('support_count'))}, conflicts={_fmt(item.get('conflict_count'))}: {item.get('label')}")
        else:
            lines.append('- none')
        next_actions = graph_summary.get('next_best_graph_actions') if isinstance(graph_summary.get('next_best_graph_actions'), list) else []
        lines.extend(['', '### Next Graph Actions', ''])
        if next_actions:
            for item in next_actions[:8]:
                lines.append(f"- {item.get('action')}: {item.get('reason')}")
        else:
            lines.append('- none')
    else:
        lines.append('- Evidence graph unavailable.')
    lines.extend(['', '## Wave History', '', '| Wave | Stop Reason | Cycles | Actions | Artifact |', '| --- | --- | ---: | --- | --- |'])
    for wave in dashboard.get('waves', []) or []:
        if not isinstance(wave, dict):
            continue
        actions = ', '.join(str(cycle.get('action')) for cycle in wave.get('cycles', []) or [] if isinstance(cycle, dict))
        path = wave.get('wave_path') or ''
        label = Path(str(path)).parent.name if path else str(wave.get('wave_id') or '')
        lines.append(f"| {label} | {_fmt(wave.get('stop_reason'))} | {_fmt(wave.get('cycle_count'))} | {actions or 'none'} | {_fmt(path)} |")
    lines.extend(['', '## Comparison Actions', ''])
    comparison_actions = dashboard.get('comparison_actions') if isinstance(dashboard.get('comparison_actions'), list) else []
    if comparison_actions:
        lines.extend(['| Time | Selected | Planned | Created | Statuses | Replay | Impact | Left | Right |', '| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |'])
        for item in comparison_actions[:10]:
            statuses = ', '.join(f'{key}:{value}' for key, value in sorted((item.get('job_status_counts') or {}).items())) or 'none'
            impact = item.get('impact') if isinstance(item.get('impact'), dict) else {}
            impact_text = f"{_fmt(impact.get('impact_label'))} runs={_fmt(impact.get('completed_runs'))} sources={_fmt(impact.get('sources_found'))} claims={_fmt(impact.get('claims_found'))}"
            replay = item.get('replay_summary') if isinstance(item.get('replay_summary'), dict) else {}
            replay_text = (
                f"replay jobs={_fmt(replay.get('replay_job_count'))} "
                f"targets={_fmt(replay.get('replayed_target_count'))} "
                f"skip={_fmt(replay.get('next_replay_duplicate_skip_count'))}"
            )
            if item.get('replay'):
                replay_text = f"replay of {_fmt(item.get('replay_of_event_id'))}; {replay_text}"
            lines.append(
                f"| {_fmt(item.get('timestamp'))} | {_fmt(item.get('selected_action'))} | {_fmt(item.get('planned_job_count'))} | {_fmt(item.get('created_job_count'))} | {statuses} | {replay_text} | {impact_text} | {_fmt(item.get('left_path'))} | {_fmt(item.get('right_path'))} |"
            )
            for recommendation in item.get('recommendations') or []:
                if isinstance(recommendation, dict):
                    lines.append(f"- Recommendation: {recommendation.get('action')}: {recommendation.get('reason')}")
    else:
        lines.append('- none')
    synthesis = director.get('synthesis') if isinstance(director.get('synthesis'), dict) else {}
    lines.extend(['', '## Synthesis', ''])
    if synthesis:
        lines.extend(
            [
                f"- OK: {_fmt(synthesis.get('ok'))}",
                f"- Bundle: {_fmt(synthesis.get('bundle_dir'))}",
                f"- Runs/sources/claims: {_fmt(synthesis.get('run_count'))} / {_fmt(synthesis.get('source_count'))} / {_fmt(synthesis.get('claim_count'))}",
            ]
        )
    else:
        lines.append('- No synthesis bundle recorded.')
    lines.append('')
    return '\n'.join(lines)


def _comparison_action_job_statuses(jobs_root: Path, event_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not event_id:
        return counts
    tag = f'comparison_event:{event_id}'
    root = jobs_root.expanduser().resolve()
    if root.exists():
        for job_path in root.glob('*/job.json'):
            job = _read_json(job_path)
            if not job or tag not in (job.get('tags') or []):
                continue
            status = str(job.get('status') or 'unknown')
            counts[status] = counts.get(status, 0) + 1
    return counts


def _comparison_action_job_impacts(jobs_root: Path, runs_root: Path, event_id: str) -> dict[str, Any]:
    if not event_id:
        return {'completed_jobs': 0, 'completed_runs': 0, 'sources_found': 0, 'claims_found': 0, 'primary_source_runs': 0, 'remaining_contradiction_runs': 0}
    tag = f'comparison_event:{event_id}'
    impact = {
        'completed_jobs': 0,
        'completed_runs': 0,
        'sources_found': 0,
        'claims_found': 0,
        'primary_source_runs': 0,
        'remaining_contradiction_runs': 0,
    }
    root = jobs_root.expanduser().resolve()
    if not root.exists():
        return impact
    for job_path in root.glob('*/job.json'):
        job = _read_json(job_path)
        if not job or tag not in (job.get('tags') or []) or str(job.get('status')) != 'completed':
            continue
        impact['completed_jobs'] += 1
        for run_id in job.get('run_ids') or []:
            loaded = load_research_run(str(run_id), root=runs_root)
            if not loaded.get('ok'):
                continue
            payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
            impact['completed_runs'] += 1
            impact['sources_found'] += len(payload.get('sources', []) or [])
            impact['claims_found'] += len(payload.get('claims', []) or [])
            source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
            if int(source_quality.get('primary_source_count') or 0) > 0:
                impact['primary_source_runs'] += 1
            contradiction = payload.get('contradiction_table') if isinstance(payload.get('contradiction_table'), dict) else {}
            unresolved = [
                item
                for item in contradiction.get('rows', []) or []
                if isinstance(item, dict) and str(item.get('status') or '').lower() not in {'resolved', 'closed'}
            ]
            if unresolved:
                impact['remaining_contradiction_runs'] += 1
    impact['impact_label'] = 'pending'
    if impact['completed_runs']:
        if impact['remaining_contradiction_runs']:
            impact['impact_label'] = 'needs_review'
        elif impact['sources_found'] or impact['claims_found']:
            impact['impact_label'] = 'evidence_added'
        else:
            impact['impact_label'] = 'no_evidence_added'
    return impact


def _comparison_action_replay_summary(jobs_root: Path, event_id: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        'replay_job_count': 0,
        'replay_event_ids': [],
        'replayed_target_count': 0,
        'replay_target_tags': [],
        'job_status_counts': {},
        'action_counts': {},
        'next_replay_duplicate_skip_count': 0,
    }
    if not event_id:
        return summary
    root = jobs_root.expanduser().resolve()
    if not root.exists():
        return summary
    source_tag = f'replay_of_comparison_event:{event_id}'
    replay_event_ids: set[str] = set()
    replay_target_tags: set[str] = set()
    status_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    for job_path in root.glob('*/job.json'):
        job = _read_json(job_path)
        tags = [str(item) for item in (job.get('tags') or [])] if job else []
        if source_tag not in tags:
            continue
        summary['replay_job_count'] += 1
        status = str(job.get('status') or 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1
        for tag in tags:
            if tag.startswith('comparison_event:'):
                replay_event_id = tag.split(':', 1)[1]
                if replay_event_id and replay_event_id != event_id:
                    replay_event_ids.add(replay_event_id)
            elif tag.startswith('replay_target:'):
                replay_target_tags.add(tag)
            elif tag.startswith('comparison_action:'):
                action = tag.split(':', 1)[1]
                action_counts[action] = action_counts.get(action, 0) + 1
    summary['replay_event_ids'] = sorted(replay_event_ids)
    summary['replay_target_tags'] = sorted(replay_target_tags)
    summary['replayed_target_count'] = len(replay_target_tags)
    summary['next_replay_duplicate_skip_count'] = len(replay_target_tags)
    summary['job_status_counts'] = dict(sorted(status_counts.items()))
    summary['action_counts'] = dict(sorted(action_counts.items()))
    return summary


def _comparison_action_recommendations(statuses: dict[str, int], impact: dict[str, Any], replay_summary: dict[str, Any] | None = None) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    failed_count = int(statuses.get('failed') or 0)
    cancelled_count = int(statuses.get('cancelled') or 0)
    running_count = int(statuses.get('running') or 0) + int(statuses.get('leased') or 0)
    queued_count = int(statuses.get('queued') or 0)
    impact_label = str(impact.get('impact_label') or '')
    replay_summary = replay_summary if isinstance(replay_summary, dict) else {}
    replay_job_count = int(replay_summary.get('replay_job_count') or 0)
    replay_statuses = replay_summary.get('job_status_counts') if isinstance(replay_summary.get('job_status_counts'), dict) else {}
    replay_failed_count = int(replay_statuses.get('failed') or 0) + int(replay_statuses.get('cancelled') or 0)
    replay_running_count = int(replay_statuses.get('running') or 0) + int(replay_statuses.get('leased') or 0)
    replay_queued_count = int(replay_statuses.get('queued') or 0)
    if failed_count or cancelled_count:
        if replay_failed_count:
            recommendations.append(
                {
                    'action': 'change_strategy_for_replayed_comparison_followups',
                    'reason': f'{replay_failed_count} replayed comparison follow-up job(s) also failed or were cancelled; switch query/source strategy before retrying again.',
                }
            )
        elif replay_queued_count or replay_running_count:
            recommendations.append(
                {
                    'action': 'wait_for_replayed_comparison_followups',
                    'reason': f'{replay_queued_count} queued and {replay_running_count} running replay job(s) are already covering failed comparison targets.',
                }
            )
        elif replay_job_count:
            recommendations.append(
                {
                    'action': 'review_replayed_comparison_followups',
                    'reason': 'Replay jobs exist for failed comparison targets; review replay impact before queuing another retry.',
                }
            )
        else:
            recommendations.append(
                {
                    'action': 'retry_failed_comparison_followups',
                    'reason': f'{failed_count} failed and {cancelled_count} cancelled comparison follow-up job(s) need retry or replacement.',
                }
            )
    if impact_label == 'no_evidence_added':
        recommendations.append(
            {
                'action': 'escalate_no_evidence_followups',
                'reason': 'Completed comparison follow-ups did not add sources or claims; broaden queries or switch source strategy.',
            }
        )
    if impact_label == 'no_evidence_added' and replay_job_count:
        recommendations.append(
            {
                'action': 'escalate_replayed_no_evidence_followups',
                'reason': 'No-evidence comparison follow-ups already have replay attempts; change source families or use manual/domain-specific discovery.',
            }
        )
    if impact_label == 'needs_review':
        recommendations.append(
            {
                'action': 'review_remaining_contradictions',
                'reason': 'Completed comparison follow-ups still contain unresolved contradiction markers.',
            }
        )
    if queued_count or running_count:
        recommendations.append(
            {
                'action': 'wait_or_start_worker',
                'reason': f'{queued_count} queued and {running_count} running comparison follow-up job(s) remain.',
            }
        )
    if not recommendations and impact_label == 'evidence_added':
        recommendations.append(
            {
                'action': 'compare_again_after_followups',
                'reason': 'Completed comparison follow-ups added evidence; rerun bundle comparison after synthesis/export.',
            }
        )
    return recommendations[:5]


def _find_comparison_action_event(director: dict[str, Any], event_id: str | None = None) -> dict[str, Any] | None:
    events = director.get('events') if isinstance(director.get('events'), list) else []
    candidates = [event for event in events if isinstance(event, dict) and event.get('event') == 'comparison_actions']
    if event_id:
        for event in candidates:
            if str(event.get('event_id') or '') == str(event_id):
                return event
        return None
    candidates.sort(key=lambda item: str(item.get('timestamp') or ''), reverse=True)
    return candidates[0] if candidates else None


def _director_comparison_action_history(director: dict[str, Any], *, jobs_root: Path | None = None, runs_root: Path | None = None, limit: int = 10) -> list[dict[str, Any]]:
    events = director.get('events') if isinstance(director.get('events'), list) else []
    history = []
    for event in events:
        if not isinstance(event, dict) or event.get('event') != 'comparison_actions':
            continue
        event_id = str(event.get('event_id') or '')
        statuses = _comparison_action_job_statuses(jobs_root, event_id) if jobs_root is not None and event_id else {}
        impact = _comparison_action_job_impacts(jobs_root, runs_root, event_id) if jobs_root is not None and runs_root is not None and event_id else {}
        replay_summary = _comparison_action_replay_summary(jobs_root, event_id) if jobs_root is not None and event_id else {}
        history.append(
            {
                'event_id': event_id,
                'timestamp': event.get('timestamp'),
                'selected_action': event.get('selected_action'),
                'left_path': event.get('left_path'),
                'right_path': event.get('right_path'),
                'replay': bool(event.get('replay')),
                'replay_of_event_id': event.get('replay_of_event_id'),
                'replay_reason': event.get('replay_reason'),
                'planned_job_count': int(event.get('planned_job_count') or 0),
                'created_job_count': int(event.get('created_job_count') or 0),
                'job_status_counts': statuses,
                'impact': impact,
                'replay_summary': replay_summary,
                'recommendations': _comparison_action_recommendations(statuses, impact, replay_summary),
            }
        )
    history.sort(key=lambda item: str(item.get('timestamp') or ''), reverse=True)
    return history[: max(1, min(100, int(limit)))]


def build_research_director_dashboard(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    apply: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    director = loaded['director']
    assessment = assess_research_director(director, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
    waves = list_director_waves(root, director_id, limit=limit)
    graph = build_director_evidence_graph(
        root,
        director_id,
        campaign_root=campaign_root,
        jobs_root=jobs_root,
        runs_root=runs_root,
        apply=False,
        limit=limit,
    )
    dashboard_dir = _safe_director_dir(root, director_id) / 'dashboard'
    dashboard_path = dashboard_dir / 'dashboard.json'
    report_path = dashboard_dir / 'dashboard.md'
    dashboard = {
        'ok': bool(assessment.get('ok')) and bool(waves.get('ok')),
        'director': _director_summary(director),
        'assessment': assessment,
        'waves': waves.get('waves', []),
        'wave_count': waves.get('total_count', 0),
        'comparison_actions': _director_comparison_action_history(director, jobs_root=jobs_root, runs_root=runs_root, limit=limit),
        'graph_summary': summarize_director_evidence_graph(graph) if graph.get('ok') else {'ok': False, 'message': graph.get('message')},
        'dashboard_path': str(dashboard_path),
        'report_path': str(report_path),
        'dry_run': not apply,
        'created_at': utc_now(),
    }
    dashboard['markdown'] = director_dashboard_markdown(dashboard)
    if apply:
        _write_json(dashboard_path, {key: value for key, value in dashboard.items() if key != 'markdown'})
        report_path.write_text(str(dashboard['markdown']), encoding='utf-8')
    return dashboard


def director_evidence_graph_markdown(graph: dict[str, Any]) -> str:
    counts = graph.get('counts') if isinstance(graph.get('counts'), dict) else {}
    director = graph.get('director') if isinstance(graph.get('director'), dict) else {}
    lines = [
        f"# Director Evidence Graph: {_fmt(director.get('director_id'))}",
        '',
        f"- Objective: {_fmt(director.get('objective'))}",
        f"- Nodes: {_fmt(counts.get('nodes'))}",
        f"- Edges: {_fmt(counts.get('edges'))}",
        f"- Runs: {_fmt(counts.get('runs'))}",
        f"- Sources: {_fmt(counts.get('sources'))}",
        f"- Claims: {_fmt(counts.get('claims'))}",
        f"- Graph JSON: {_fmt(graph.get('graph_path'))}",
        '',
        '## Node Counts',
        '',
    ]
    by_kind = counts.get('nodes_by_kind') if isinstance(counts.get('nodes_by_kind'), dict) else {}
    if by_kind:
        for kind, count in sorted(by_kind.items()):
            lines.append(f'- {kind}: {count}')
    else:
        lines.append('- none')
    lines.extend(['', '## Edge Counts', ''])
    by_relation = counts.get('edges_by_relation') if isinstance(counts.get('edges_by_relation'), dict) else {}
    if by_relation:
        for relation, count in sorted(by_relation.items()):
            lines.append(f'- {relation}: {count}')
    else:
        lines.append('- none')
    lines.append('')
    return '\n'.join(lines)


def summarize_director_evidence_graph(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get('nodes') if isinstance(graph.get('nodes'), list) else []
    edges = graph.get('edges') if isinstance(graph.get('edges'), list) else []
    node_by_id = {str(node.get('id')): node for node in nodes if isinstance(node, dict) and node.get('id')}
    supported_by: dict[str, list[str]] = {}
    conflicted_by: dict[str, list[str]] = {}
    read_sources: dict[str, list[str]] = {}
    contradiction_edges: dict[str, list[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get('source') or '')
        target = str(edge.get('target') or '')
        relation = str(edge.get('relation') or '')
        if relation == 'supported_by':
            supported_by.setdefault(source, []).append(target)
        elif relation == 'conflicted_by':
            conflicted_by.setdefault(source, []).append(target)
        elif relation == 'read_source':
            read_sources.setdefault(source, []).append(target)
        elif relation == 'has_contradiction':
            contradiction_edges.setdefault(source, []).append(target)

    claims = [node for node in nodes if isinstance(node, dict) and node.get('kind') == 'claim']
    central_claims = []
    weak_evidence_claims = []
    for claim in claims:
        claim_id = str(claim.get('id') or '')
        support_count = len(supported_by.get(claim_id, []))
        conflict_count = len(conflicted_by.get(claim_id, []))
        item = {
            'id': claim_id,
            'label': claim.get('label'),
            'support_count': support_count,
            'conflict_count': conflict_count,
            'confidence': claim.get('confidence'),
        }
        central_claims.append(item)
        if support_count == 0 or conflict_count > 0:
            weak_evidence_claims.append(item)
    central_claims.sort(key=lambda item: (int(item.get('support_count') or 0) + int(item.get('conflict_count') or 0), int(item.get('support_count') or 0)), reverse=True)
    weak_evidence_claims.sort(key=lambda item: (int(item.get('conflict_count') or 0), -int(item.get('support_count') or 0)), reverse=True)

    domains: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or node.get('kind') not in {'source', 'memory_source'}:
            continue
        domain = str(node.get('domain') or '').strip()
        if not domain:
            continue
        bucket = domains.setdefault(domain, {'domain': domain, 'source_count': 0, 'source_ids': []})
        bucket['source_count'] += 1
        bucket['source_ids'].append(node.get('id'))
    repeated_domains = [item for item in domains.values() if int(item.get('source_count') or 0) > 1]
    repeated_domains.sort(key=lambda item: int(item.get('source_count') or 0), reverse=True)

    unresolved_chains = []
    for run_id, contradiction_ids in contradiction_edges.items():
        for contradiction_id in contradiction_ids:
            contradiction = node_by_id.get(contradiction_id, {})
            status = str(contradiction.get('status') or '').lower()
            if status in {'resolved', 'closed'}:
                continue
            unresolved_chains.append(
                {
                    'run_node': run_id,
                    'contradiction_id': contradiction_id,
                    'label': contradiction.get('label'),
                    'status': contradiction.get('status') or 'unresolved',
                }
            )

    next_actions = []
    if weak_evidence_claims:
        next_actions.append(
            {
                'action': 'create_followup_for_weak_claims',
                'reason': f"{len(weak_evidence_claims)} claim(s) have no support or conflicting sources.",
                'suggested_tool': 'safe_research_director action: advance',
            }
        )
    if unresolved_chains:
        next_actions.append(
            {
                'action': 'resolve_contradictions',
                'reason': f"{len(unresolved_chains)} unresolved contradiction chain(s) remain.",
                'suggested_tool': 'safe_research_director action: advance',
            }
        )
    if repeated_domains:
        next_actions.append(
            {
                'action': 'diversify_source_domains',
                'reason': f"{len(repeated_domains)} source domain(s) appear multiple times; add independent corroboration.",
                'suggested_tool': 'safe_research_director action: advance',
            }
        )
    if not next_actions:
        next_actions.append({'action': 'synthesize_or_export', 'reason': 'Evidence graph has no obvious weak claim, contradiction, or domain concentration signals.'})

    return {
        'ok': True,
        'graph_path': graph.get('graph_path'),
        'counts': graph.get('counts') if isinstance(graph.get('counts'), dict) else {},
        'central_claims': central_claims[:10],
        'weak_evidence_claims': weak_evidence_claims[:10],
        'repeated_source_domains': repeated_domains[:10],
        'unresolved_contradiction_chains': unresolved_chains[:10],
        'next_best_graph_actions': next_actions[:10],
    }


def _graph_action_matches(selected: str, action: str) -> bool:
    selected = str(selected or 'all').strip().lower()
    action = str(action or '').strip().lower()
    if selected in {'', 'all', '*'}:
        return True
    aliases = {
        'weak_claims': 'create_followup_for_weak_claims',
        'weak': 'create_followup_for_weak_claims',
        'claims': 'create_followup_for_weak_claims',
        'contradictions': 'resolve_contradictions',
        'contradiction': 'resolve_contradictions',
        'domains': 'diversify_source_domains',
        'diversify': 'diversify_source_domains',
    }
    return aliases.get(selected, selected) == action


def _comparison_action_matches(selected: str, action: str) -> bool:
    selected = str(selected or 'all').strip().lower()
    action = str(action or '').strip().lower()
    if selected in {'', 'all', '*'}:
        return True
    aliases = {
        'gaps': 'investigate_new_gaps',
        'gap': 'investigate_new_gaps',
        'weak_claims': 'investigate_new_gaps',
        'contradictions': 'resolve_new_contradictions',
        'contradiction': 'resolve_new_contradictions',
        'sources': 'recover_lost_source_coverage',
        'source_coverage': 'recover_lost_source_coverage',
        'coverage': 'recover_lost_source_coverage',
    }
    return aliases.get(selected, selected) == action


def _plan_graph_action_jobs(graph_summary: dict[str, Any], *, selected_action: str, max_actions: int) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    max_actions = max(0, int(max_actions))
    if max_actions <= 0:
        return plans
    if _graph_action_matches(selected_action, 'create_followup_for_weak_claims'):
        for item in graph_summary.get('weak_evidence_claims', []) or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get('label') or '').strip()
            if not label:
                continue
            plans.append(
                {
                    'graph_action': 'create_followup_for_weak_claims',
                    'reason': 'weak_evidence_claim',
                    'target_id': item.get('id'),
                    'request': f"Find independent evidence, primary sources, and counterevidence for this weak or disputed claim: {label}",
                }
            )
            if len(plans) >= max_actions:
                return plans
    if _graph_action_matches(selected_action, 'resolve_contradictions'):
        for item in graph_summary.get('unresolved_contradiction_chains', []) or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get('label') or '').strip()
            if not label:
                continue
            plans.append(
                {
                    'graph_action': 'resolve_contradictions',
                    'reason': 'unresolved_contradiction_chain',
                    'target_id': item.get('contradiction_id'),
                    'request': f"Resolve this unresolved contradiction using primary and independent sources: {label}",
                }
            )
            if len(plans) >= max_actions:
                return plans
    if _graph_action_matches(selected_action, 'diversify_source_domains'):
        for item in graph_summary.get('repeated_source_domains', []) or []:
            if not isinstance(item, dict):
                continue
            domain = str(item.get('domain') or '').strip()
            if not domain:
                continue
            plans.append(
                {
                    'graph_action': 'diversify_source_domains',
                    'reason': 'repeated_source_domain',
                    'target_id': domain,
                    'request': f"Find independent corroborating sources that do not rely on the repeated domain {domain}. Prioritize official, primary, academic, regulatory, or documentation sources.",
                }
            )
            if len(plans) >= max_actions:
                return plans
    return plans


def _plan_comparison_action_jobs(comparison: dict[str, Any], *, selected_action: str, max_actions: int) -> list[dict[str, Any]]:
    max_actions = max(0, int(max_actions))
    if max_actions <= 0:
        return []
    gap_plans: list[dict[str, Any]] = []
    contradiction_plans: list[dict[str, Any]] = []
    source_plans: list[dict[str, Any]] = []
    if _comparison_action_matches(selected_action, 'investigate_new_gaps'):
        for item in comparison.get('remaining_gaps', []) or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get('label') or '').strip()
            if not label:
                continue
            gap_plans.append(
                {
                    'comparison_action': 'investigate_new_gaps',
                    'reason': str(item.get('kind') or 'remaining_gap'),
                    'target_id': item.get('id'),
                    'request': f"Investigate this newly introduced or still unresolved comparison gap with primary and independent evidence: {label}",
                }
            )
    if _comparison_action_matches(selected_action, 'resolve_new_contradictions'):
        for label in comparison.get('new_unresolved_contradictions', []) or []:
            text = str(label or '').strip()
            if not text:
                continue
            contradiction_plans.append(
                {
                    'comparison_action': 'resolve_new_contradictions',
                    'reason': 'new_unresolved_contradiction',
                    'target_id': text,
                    'request': f"Resolve this newly introduced unresolved contradiction from the bundle comparison using primary and independent sources: {text}",
                }
            )
    if _comparison_action_matches(selected_action, 'recover_lost_source_coverage'):
        for source in comparison.get('removed_sources', []) or []:
            text = str(source or '').strip()
            if not text:
                continue
            source_plans.append(
                {
                    'comparison_action': 'recover_lost_source_coverage',
                    'reason': 'removed_source',
                    'target_id': text,
                    'request': f"Recover or replace lost source coverage from the bundle comparison. Find current equivalent, primary, or independent sources for: {text}",
                }
            )
        for item in comparison.get('domain_coverage_changes', []) or []:
            if not isinstance(item, dict):
                continue
            domain = str(item.get('domain') or '').strip()
            if not domain:
                continue
            left_count = int(item.get('left_count') or 0)
            right_count = int(item.get('right_count') or 0)
            if right_count >= left_count:
                continue
            source_plans.append(
                {
                    'comparison_action': 'recover_lost_source_coverage',
                    'reason': 'domain_coverage_loss',
                    'target_id': domain,
                    'request': f"Recover lost source coverage for domain {domain}. Find replacement independent or primary sources because coverage dropped from {left_count} to {right_count}.",
                }
            )
    plans: list[dict[str, Any]] = []
    buckets = [gap_plans, contradiction_plans, source_plans]
    while len(plans) < max_actions and any(buckets):
        for bucket in buckets:
            if bucket and len(plans) < max_actions:
                plans.append(bucket.pop(0))
    return plans


def _replay_target_tag(plan: dict[str, Any]) -> str:
    raw = f"{plan.get('comparison_action')}:{plan.get('target_id')}"
    slug = re.sub(r'[^a-zA-Z0-9_.:-]+', '-', raw).strip('-').lower()
    return f"replay_target:{slug[:120] or 'unknown'}"


def _existing_replay_target_tags(jobs_root: Path, event_id: str) -> set[str]:
    tags: set[str] = set()
    root = jobs_root.expanduser().resolve()
    source_tag = f'replay_of_comparison_event:{event_id}'
    if root.exists():
        for job_path in root.glob('*/job.json'):
            job = _read_json(job_path)
            job_tags = [str(item) for item in (job.get('tags') or [])] if job else []
            if source_tag not in job_tags:
                continue
            tags.update(tag for tag in job_tags if tag.startswith('replay_target:'))
    return tags


def _replay_comparison_action_plans(plans: list[dict[str, Any]], *, event_id: str, reason: str, existing_target_tags: set[str] | None = None) -> list[dict[str, Any]]:
    replay_plans = []
    existing_target_tags = set(existing_target_tags or set())
    for plan in plans:
        target_tag = _replay_target_tag(plan)
        if target_tag in existing_target_tags:
            continue
        replay = dict(plan)
        replay['replay_of_event_id'] = event_id
        replay['replay_reason'] = reason
        replay['replay_target_tag'] = target_tag
        replay['request'] = (
            f"REPLAY comparison follow-up after {reason}. Prior attempt did not fully resolve the diff. "
            f"Use a broader query strategy, prioritize primary/official/independent sources, explicitly document failures, "
            f"and return clear evidence for the target.\n\nOriginal task: {plan.get('request')}"
        )
        replay_plans.append(replay)
        existing_target_tags.add(target_tag)
    return replay_plans


def execute_director_graph_actions(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    apply: bool = False,
    selected_action: str = 'all',
    max_actions: int = 3,
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    director = dict(loaded['director'])
    assessment = assess_research_director(director, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
    graph = build_director_evidence_graph(
        root,
        director_id,
        campaign_root=campaign_root,
        jobs_root=jobs_root,
        runs_root=runs_root,
        apply=False,
    )
    if not graph.get('ok'):
        return graph
    graph_summary = summarize_director_evidence_graph(graph)
    gate = assessment.get('quality_gate') if isinstance(assessment.get('quality_gate'), dict) else {}
    remaining_budget = max(0, int(gate.get('remaining_followup_budget') or 0))
    max_actions = max(0, min(int(max_actions), remaining_budget))
    planned_jobs = _plan_graph_action_jobs(graph_summary, selected_action=selected_action, max_actions=max_actions)
    objective_memory = director.get('objective_memory') if isinstance(director.get('objective_memory'), dict) else {}
    created_jobs = []
    if apply and planned_jobs:
        event_id = uuid.uuid4().hex[:12]
        for plan in planned_jobs:
            created = create_research_job(
                jobs_root,
                request=_request_with_memory_hints(str(plan['request']), objective_memory),
                profile=str(director.get('profile') or 'careful'),
                priority=int(director.get('priority') or 0),
                tags=[
                    f"director:{director_id}",
                    f"campaign:{director.get('campaign_id')}",
                    'director_followup',
                    'director_graph_action',
                    f"director_reason:{plan.get('reason')}",
                    f"graph_action:{plan.get('graph_action')}",
                ],
            )
            created_jobs.append(created)
            if created.get('ok'):
                director.setdefault('followup_job_ids', []).append(created['job']['job_id'])
        director['updated_at'] = utc_now()
        director.setdefault('events', []).append(
            {
                'timestamp': director['updated_at'],
                'event': 'graph_actions',
                'selected_action': selected_action,
                'planned_job_count': len(planned_jobs),
                'created_job_count': sum(1 for item in created_jobs if item.get('ok')),
            }
        )
        _write_json(Path(str(director['director_path'])), director)
    return {
        'ok': True,
        'dry_run': not apply,
        'director': _director_summary(director),
        'selected_action': selected_action,
        'remaining_followup_budget': remaining_budget,
        'graph_summary': graph_summary,
        'planned_jobs': planned_jobs,
        'created_jobs': created_jobs,
        'message': 'Preview only. Add apply=true to queue graph-action follow-up jobs.' if not apply else 'Graph-action follow-up jobs queued.',
    }


def execute_director_comparison_actions(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    left: Path,
    right: Path,
    apply: bool = False,
    selected_action: str = 'all',
    max_actions: int = 3,
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    director = dict(loaded['director'])
    assessment = assess_research_director(director, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
    comparison = compare_director_bundles(root, director_id, left=left, right=right, apply=False)
    if not comparison.get('ok'):
        return comparison
    gate = assessment.get('quality_gate') if isinstance(assessment.get('quality_gate'), dict) else {}
    remaining_budget = max(0, int(gate.get('remaining_followup_budget') or 0))
    max_actions = max(0, min(int(max_actions), remaining_budget))
    planned_jobs = _plan_comparison_action_jobs(comparison, selected_action=selected_action, max_actions=max_actions)
    objective_memory = director.get('objective_memory') if isinstance(director.get('objective_memory'), dict) else {}
    created_jobs = []
    if apply and planned_jobs:
        event_id = uuid.uuid4().hex[:12]
        for plan in planned_jobs:
            created = create_research_job(
                jobs_root,
                request=_request_with_memory_hints(str(plan['request']), objective_memory),
                profile=str(director.get('profile') or 'careful'),
                priority=int(director.get('priority') or 0),
                tags=[
                    f"director:{director_id}",
                    f"campaign:{director.get('campaign_id')}",
                    'director_followup',
                    'director_comparison_action',
                    f'comparison_event:{event_id}',
                    f"director_reason:{plan.get('reason')}",
                    f"comparison_action:{plan.get('comparison_action')}",
                ],
            )
            created_jobs.append(created)
            if created.get('ok'):
                director.setdefault('followup_job_ids', []).append(created['job']['job_id'])
        director['updated_at'] = utc_now()
        director.setdefault('events', []).append(
            {
                'event_id': event_id,
                'timestamp': director['updated_at'],
                'event': 'comparison_actions',
                'selected_action': selected_action,
                'left_path': str(left),
                'right_path': str(right),
                'planned_job_count': len(planned_jobs),
                'created_job_count': sum(1 for item in created_jobs if item.get('ok')),
            }
        )
        _write_json(Path(str(director['director_path'])), director)
    return {
        'ok': True,
        'dry_run': not apply,
        'director': _director_summary(director),
        'selected_action': selected_action,
        'remaining_followup_budget': remaining_budget,
        'comparison': {key: value for key, value in comparison.items() if key != 'markdown'},
        'planned_jobs': planned_jobs,
        'created_jobs': created_jobs,
        'message': 'Preview only. Add apply=true to queue comparison-action follow-up jobs.' if not apply else 'Comparison-action follow-up jobs queued.',
    }


def replay_director_comparison_actions(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    event_id: str | None = None,
    apply: bool = False,
    selected_action: str = 'all',
    max_actions: int = 3,
    replay_reason: str = 'failed_or_no_evidence_followup',
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    director = dict(loaded['director'])
    source_event = _find_comparison_action_event(director, event_id)
    if not source_event:
        return {'ok': False, 'message': 'No comparison-action event found to replay.', 'director': _director_summary(director)}
    left = Path(str(source_event.get('left_path') or ''))
    right = Path(str(source_event.get('right_path') or ''))
    if not str(left) or not str(right):
        return {'ok': False, 'message': 'Comparison-action event is missing left/right bundle paths.', 'director': _director_summary(director)}
    assessment = assess_research_director(director, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
    comparison = compare_director_bundles(root, director_id, left=left, right=right, apply=False)
    if not comparison.get('ok'):
        return comparison
    gate = assessment.get('quality_gate') if isinstance(assessment.get('quality_gate'), dict) else {}
    remaining_budget = max(0, int(gate.get('remaining_followup_budget') or 0))
    requested_actions = max(0, min(int(max_actions), remaining_budget))
    source_event_id = str(source_event.get('event_id') or '')
    existing_target_tags = _existing_replay_target_tags(jobs_root, source_event_id)
    search_limit = max(requested_actions + len(existing_target_tags), requested_actions)
    base_plans = _plan_comparison_action_jobs(comparison, selected_action=selected_action, max_actions=search_limit)
    planned_jobs = _replay_comparison_action_plans(base_plans, event_id=source_event_id, reason=replay_reason, existing_target_tags=existing_target_tags)
    planned_jobs = planned_jobs[:requested_actions]
    objective_memory = director.get('objective_memory') if isinstance(director.get('objective_memory'), dict) else {}
    created_jobs = []
    if apply and planned_jobs:
        replay_event_id = uuid.uuid4().hex[:12]
        for plan in planned_jobs:
            created = create_research_job(
                jobs_root,
                request=_request_with_memory_hints(str(plan['request']), objective_memory),
                profile=str(director.get('profile') or 'careful'),
                priority=int(director.get('priority') or 0) + 1,
                tags=[
                    f"director:{director_id}",
                    f"campaign:{director.get('campaign_id')}",
                    'director_followup',
                    'director_comparison_action',
                    'director_comparison_replay',
                    f'comparison_event:{replay_event_id}',
                    f'replay_of_comparison_event:{source_event_id}',
                    str(plan.get('replay_target_tag') or ''),
                    f"director_reason:{plan.get('reason')}",
                    f"comparison_action:{plan.get('comparison_action')}",
                ],
            )
            created_jobs.append(created)
            if created.get('ok'):
                director.setdefault('followup_job_ids', []).append(created['job']['job_id'])
        director['updated_at'] = utc_now()
        director.setdefault('events', []).append(
            {
                'event_id': replay_event_id,
                'timestamp': director['updated_at'],
                'event': 'comparison_actions',
                'replay': True,
                'replay_of_event_id': source_event_id,
                'replay_reason': replay_reason,
                'selected_action': selected_action,
                'left_path': str(left),
                'right_path': str(right),
                'planned_job_count': len(planned_jobs),
                'created_job_count': sum(1 for item in created_jobs if item.get('ok')),
            }
        )
        _write_json(Path(str(director['director_path'])), director)
    return {
        'ok': True,
        'dry_run': not apply,
        'director': _director_summary(director),
        'source_event': source_event,
        'selected_action': selected_action,
        'remaining_followup_budget': remaining_budget,
        'comparison': {key: value for key, value in comparison.items() if key != 'markdown'},
        'planned_jobs': planned_jobs,
        'created_jobs': created_jobs,
        'message': 'Preview only. Add apply=true to replay comparison-action follow-up jobs.' if not apply else 'Comparison-action replay jobs queued.',
    }


def _director_command_examples(director_id: str, dashboard: dict[str, Any], recovery: dict[str, Any], graph_actions: dict[str, Any]) -> list[dict[str, str]]:
    commands = [
        {
            'label': 'Refresh dashboard',
            'command': f'safe_research_director("director_id: {director_id}\\naction: dashboard")',
        },
        {
            'label': 'Write dashboard artifacts',
            'command': f'safe_research_director("director_id: {director_id}\\naction: dashboard\\napply=true")',
        },
        {
            'label': 'Write evidence graph',
            'command': f'safe_research_director("director_id: {director_id}\\naction: graph\\napply=true")',
        },
        {
            'label': 'Preview recovery',
            'command': f'safe_research_director("director_id: {director_id}\\naction: recovery\\npolicy=balanced")',
        },
    ]
    gate = dashboard.get('assessment', {}).get('quality_gate') if isinstance(dashboard.get('assessment'), dict) else {}
    if isinstance(gate, dict) and gate.get('recommended_action') == 'continue':
        commands.append(
            {
                'label': 'Queue standard director follow-ups',
                'command': f'safe_research_director("director_id: {director_id}\\naction: advance\\nmax_followups=3\\napply=true")',
            }
        )
    if graph_actions.get('planned_jobs'):
        commands.append(
            {
                'label': 'Queue graph-targeted follow-ups',
                'command': f'safe_research_director("director_id: {director_id}\\naction: graph_actions\\nmax_actions=3\\napply=true")',
            }
        )
    if recovery.get('issue_counts', {}).get('stuck_jobs'):
        commands.append(
            {
                'label': 'Apply aggressive recovery',
                'command': f'safe_research_director("director_id: {director_id}\\naction: recovery\\npolicy=aggressive\\napply=true")',
            }
        )
    if isinstance(gate, dict) and gate.get('recommended_action') == 'synthesize':
        commands.append(
            {
                'label': 'Write campaign synthesis',
                'command': f'safe_research_director("director_id: {director_id}\\naction: synthesize\\napply=true")',
            }
        )
    return commands


def _runbook_remediation_learning_snapshot(root: Path, director_id: str, dashboard: dict[str, Any]) -> dict[str, Any]:
    assessment = dashboard.get('assessment') if isinstance(dashboard.get('assessment'), dict) else {}
    learning = assessment.get('remediation_strategy_learning') if isinstance(assessment.get('remediation_strategy_learning'), dict) else {}
    store_path = root.expanduser().resolve() / REMEDIATION_STRATEGY_LEARNING_FILE
    store = _read_json(store_path)
    directors = store.get('directors') if isinstance(store.get('directors'), dict) else {}
    current_director_learning = directors.get(director_id) if isinstance(directors.get(director_id), dict) else {}
    aggregate = store.get('aggregate') if isinstance(store.get('aggregate'), dict) else _merge_remediation_strategy_learning(
        *[item for item in directors.values() if isinstance(item, dict)],
        source='shared',
    )
    return {
        'ok': True,
        'store_path': str(store_path),
        'store_exists': store_path.exists(),
        'schema_version': 1,
        'director_id': director_id,
        'director_count': int(store.get('director_count') or len(directors)),
        'strategy_count': int(store.get('strategy_count') or aggregate.get('strategy_count') or 0),
        'current_director': {
            'strategy_count': int(current_director_learning.get('strategy_count') or 0),
            'strategies': list(current_director_learning.get('strategies') or []),
        },
        'combined_assessment_learning': learning,
        'shared_aggregate': aggregate,
        'top_shared_strategies': list(aggregate.get('strategies') or [])[:20],
    }


def director_runbook_markdown(runbook: dict[str, Any]) -> str:
    director = runbook.get('director') if isinstance(runbook.get('director'), dict) else {}
    dashboard = runbook.get('dashboard') if isinstance(runbook.get('dashboard'), dict) else {}
    graph_summary = dashboard.get('graph_summary') if isinstance(dashboard.get('graph_summary'), dict) else {}
    recovery = runbook.get('recovery') if isinstance(runbook.get('recovery'), dict) else {}
    graph_actions = runbook.get('graph_actions') if isinstance(runbook.get('graph_actions'), dict) else {}
    comparison_actions = dashboard.get('comparison_actions') if isinstance(dashboard.get('comparison_actions'), list) else []
    remediation_learning = runbook.get('remediation_learning') if isinstance(runbook.get('remediation_learning'), dict) else {}
    gate = dashboard.get('assessment', {}).get('quality_gate') if isinstance(dashboard.get('assessment'), dict) else {}
    lines = [
        f"# Research Director Runbook: {_fmt(director.get('director_id'))}",
        '',
        f"- Objective: {_fmt(director.get('objective'))}",
        f"- Status: {_fmt(director.get('status'))}",
        f"- Current gate action: {_fmt(gate.get('recommended_action') if isinstance(gate, dict) else None)}",
        f"- Dashboard: {_fmt(dashboard.get('report_path'))}",
        f"- Evidence graph: {_fmt(runbook.get('evidence_graph', {}).get('graph_path') if isinstance(runbook.get('evidence_graph'), dict) else None)}",
        f"- Runbook JSON: {_fmt(runbook.get('runbook_path'))}",
        '',
        '## Recovery State',
        '',
        f"- Stale waves: {_fmt(recovery.get('issue_counts', {}).get('stale_waves') if isinstance(recovery.get('issue_counts'), dict) else None)}",
        f"- Failed worker waves: {_fmt(recovery.get('issue_counts', {}).get('failed_worker_waves') if isinstance(recovery.get('issue_counts'), dict) else None)}",
        f"- Stuck jobs: {_fmt(recovery.get('issue_counts', {}).get('stuck_jobs') if isinstance(recovery.get('issue_counts'), dict) else None)}",
        f"- Interrupted checkpoints: {_fmt(recovery.get('issue_counts', {}).get('interrupted_checkpoints') if isinstance(recovery.get('issue_counts'), dict) else None)}",
        '',
        '## Graph Summary',
        '',
        f"- Central claims: {_fmt(len(graph_summary.get('central_claims') or []))}",
        f"- Weak evidence claims: {_fmt(len(graph_summary.get('weak_evidence_claims') or []))}",
        f"- Repeated source domains: {_fmt(len(graph_summary.get('repeated_source_domains') or []))}",
        f"- Unresolved contradiction chains: {_fmt(len(graph_summary.get('unresolved_contradiction_chains') or []))}",
        f"- Planned graph-action jobs: {_fmt(len(graph_actions.get('planned_jobs') or []))}",
        f"- Comparison-action events: {_fmt(len(comparison_actions))}",
        '',
        '## Remediation Learning Export',
        '',
        f"- Snapshot: {_fmt(runbook.get('remediation_learning_path'))}",
        f"- Shared store: {_fmt(remediation_learning.get('store_path'))}",
        f"- Shared store exists: {_fmt(remediation_learning.get('store_exists'))}",
        f"- Directors/strategies: {_fmt(remediation_learning.get('director_count'))} / {_fmt(remediation_learning.get('strategy_count'))}",
        '',
    ]
    top_strategies = remediation_learning.get('top_shared_strategies') if isinstance(remediation_learning.get('top_shared_strategies'), list) else []
    if top_strategies:
        lines.extend(['| Gap | Strategy | Success | Attempts | Delta |', '| --- | --- | ---: | ---: | ---: |'])
        for item in top_strategies[:8]:
            if isinstance(item, dict):
                lines.append(
                    f"| {_fmt(item.get('gap_code'))} | {_fmt(item.get('strategy'))} | "
                    f"{_fmt(item.get('success_rate'))} | {_fmt(item.get('attempts'))} | {_fmt(item.get('learned_priority_delta'))} |"
                )
    else:
        lines.append('- none')
    lines.extend(
        [
            '',
            '## Comparison Action History',
            '',
        ]
    )
    if comparison_actions:
        lines.extend(['| Time | Selected | Planned | Created | Statuses | Replay | Impact | Left | Right |', '| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |'])
        for item in comparison_actions[:10]:
            statuses = ', '.join(f'{key}:{value}' for key, value in sorted((item.get('job_status_counts') or {}).items())) or 'none'
            impact = item.get('impact') if isinstance(item.get('impact'), dict) else {}
            impact_text = f"{_fmt(impact.get('impact_label'))} runs={_fmt(impact.get('completed_runs'))} sources={_fmt(impact.get('sources_found'))} claims={_fmt(impact.get('claims_found'))}"
            replay = item.get('replay_summary') if isinstance(item.get('replay_summary'), dict) else {}
            replay_text = (
                f"replay jobs={_fmt(replay.get('replay_job_count'))} "
                f"targets={_fmt(replay.get('replayed_target_count'))} "
                f"skip={_fmt(replay.get('next_replay_duplicate_skip_count'))}"
            )
            if item.get('replay'):
                replay_text = f"replay of {_fmt(item.get('replay_of_event_id'))}; {replay_text}"
            lines.append(
                f"| {_fmt(item.get('timestamp'))} | {_fmt(item.get('selected_action'))} | {_fmt(item.get('planned_job_count'))} | {_fmt(item.get('created_job_count'))} | {statuses} | {replay_text} | {impact_text} | {_fmt(item.get('left_path'))} | {_fmt(item.get('right_path'))} |"
            )
            for recommendation in item.get('recommendations') or []:
                if isinstance(recommendation, dict):
                    lines.append(f"- Recommendation: {recommendation.get('action')}: {recommendation.get('reason')}")
    else:
        lines.append('- none')
    lines.extend(['', '## Exact Next Commands', ''])
    commands = runbook.get('commands') if isinstance(runbook.get('commands'), list) else []
    if commands:
        for item in commands:
            lines.extend([f"### {item.get('label')}", '', '```text', str(item.get('command') or ''), '```', ''])
    else:
        lines.append('- none')
    lines.append('')
    return '\n'.join(lines)


def build_director_runbook(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    apply: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    dashboard = build_research_director_dashboard(
        root,
        director_id,
        campaign_root=campaign_root,
        jobs_root=jobs_root,
        runs_root=runs_root,
        apply=apply,
        limit=limit,
    )
    graph = build_director_evidence_graph(
        root,
        director_id,
        campaign_root=campaign_root,
        jobs_root=jobs_root,
        runs_root=runs_root,
        apply=apply,
        limit=limit,
    )
    recovery = recover_research_director(
        root,
        director_id,
        campaign_root=campaign_root,
        jobs_root=jobs_root,
        runs_root=runs_root,
        apply=False,
        policy='balanced',
    )
    graph_actions = execute_director_graph_actions(
        root,
        director_id,
        campaign_root=campaign_root,
        jobs_root=jobs_root,
        runs_root=runs_root,
        apply=False,
        selected_action='all',
        max_actions=3,
    )
    director = _director_summary(loaded['director'])
    runbook_dir = _safe_director_dir(root, director_id) / 'runbook'
    runbook_path = runbook_dir / 'runbook.json'
    report_path = runbook_dir / 'runbook.md'
    remediation_learning_path = runbook_dir / 'remediation_learning.json'
    remediation_learning = _runbook_remediation_learning_snapshot(root, director_id, dashboard)
    commands = _director_command_examples(director_id, dashboard, recovery, graph_actions)
    runbook = {
        'ok': bool(dashboard.get('ok')) and bool(graph.get('ok')) and bool(recovery.get('ok')) and bool(graph_actions.get('ok')),
        'dry_run': not apply,
        'director': director,
        'dashboard': {key: value for key, value in dashboard.items() if key != 'markdown'},
        'evidence_graph': {key: value for key, value in graph.items() if key not in {'markdown', 'nodes', 'edges'}},
        'recovery': recovery,
        'graph_actions': graph_actions,
        'remediation_learning': remediation_learning,
        'commands': commands,
        'runbook_path': str(runbook_path),
        'report_path': str(report_path),
        'remediation_learning_path': str(remediation_learning_path),
        'created_at': utc_now(),
    }
    runbook['markdown'] = director_runbook_markdown(runbook)
    if apply:
        runbook_dir.mkdir(parents=True, exist_ok=True)
        _write_json(runbook_path, {key: value for key, value in runbook.items() if key != 'markdown'})
        _write_json(remediation_learning_path, remediation_learning)
        report_path.write_text(str(runbook['markdown']), encoding='utf-8')
    return runbook


def _runbook_export_profile(profile: str, *, redact: bool | None = None, archive: bool | None = None) -> dict[str, Any]:
    normalized = str(profile or 'full-fidelity').strip().lower().replace('_', '-')
    if normalized in {'private', 'share', 'private-share'}:
        normalized = 'private-share'
        default_redact = True
        default_archive = True
    else:
        normalized = 'full-fidelity'
        default_redact = False
        default_archive = True
    return {
        'name': normalized,
        'redact': default_redact if redact is None else bool(redact),
        'archive': default_archive if archive is None else bool(archive),
    }


def _export_artifact(
    *,
    source_path: Path,
    output_path: Path,
    redact: bool,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.suffix.lower() == '.json':
        payload = _read_json(source_path)
        if redact:
            payload = _redact_json(payload)
        _write_json(output_path, payload)
    else:
        text = source_path.read_text(encoding='utf-8')
        output_path.write_text(_redact_text(text) if redact else text, encoding='utf-8')
    return {
        'path': str(output_path),
        'source_path': str(source_path),
        'sha256': _sha256_file(output_path),
        'bytes': output_path.stat().st_size,
        'redacted': redact,
    }


def _portable_export_record(file_info: dict[str, Any]) -> dict[str, Any]:
    return {
        'relative_path': file_info.get('relative_path'),
        'sha256': file_info.get('sha256'),
        'bytes': file_info.get('bytes'),
        'redacted': bool(file_info.get('redacted')),
    }


def _portable_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ''
    info.gname = ''
    info.mtime = 0
    if info.isfile():
        info.mode = 0o644
    elif info.isdir():
        info.mode = 0o755
    return info


def export_director_runbook(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    apply: bool = False,
    profile: str = 'full-fidelity',
    redact: bool | None = None,
    archive: bool | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    selected_profile = _runbook_export_profile(profile, redact=redact, archive=archive)
    export_id = f'{utc_now().replace(":", "").replace("-", "")}-{selected_profile["name"]}'.lower()
    export_dir = _safe_director_dir(root, director_id) / 'runbook_exports' / export_id
    manifest_path = export_dir / 'manifest.json'
    archive_path = export_dir.with_suffix('.tar.gz')
    planned_files = [
        'runbook.json',
        'runbook.md',
        'dashboard/dashboard.json',
        'dashboard/dashboard.md',
        'evidence_graph/evidence_graph.json',
        'evidence_graph/index.md',
        'remediation_learning/remediation_learning.json',
        'remediation_learning/remediation_strategy_learning.json',
        'manifest.json',
    ]
    if not apply:
        redact_paths = bool(selected_profile['redact'])
        return {
            'ok': True,
            'dry_run': True,
            'director': _director_summary(loaded['director']),
            'profile': selected_profile,
            'export_dir': _redact_text(export_dir) if redact_paths else str(export_dir),
            'manifest_path': _redact_text(manifest_path) if redact_paths else str(manifest_path),
            'archive_path': (_redact_text(archive_path) if redact_paths else str(archive_path)) if selected_profile['archive'] else None,
            'planned_files': planned_files + (['archive.tar.gz'] if selected_profile['archive'] else []),
            'message': 'Preview only. Add apply=true to write the runbook export bundle.',
        }

    runbook = build_director_runbook(
        root,
        director_id,
        campaign_root=campaign_root,
        jobs_root=jobs_root,
        runs_root=runs_root,
        apply=True,
        limit=limit,
    )
    if not runbook.get('ok'):
        return runbook
    source_files = [
        ('runbook.json', Path(str(runbook['runbook_path']))),
        ('runbook.md', Path(str(runbook['report_path']))),
        ('dashboard/dashboard.json', Path(str(runbook['dashboard']['dashboard_path']))),
        ('dashboard/dashboard.md', Path(str(runbook['dashboard']['report_path']))),
        ('evidence_graph/evidence_graph.json', Path(str(runbook['evidence_graph']['graph_path']))),
        ('evidence_graph/index.md', Path(str(runbook['evidence_graph']['index_path']))),
        ('remediation_learning/remediation_learning.json', Path(str(runbook['remediation_learning_path']))),
    ]
    shared_learning_path = Path(str(runbook.get('remediation_learning', {}).get('store_path') or ''))
    if shared_learning_path.exists():
        source_files.append(('remediation_learning/remediation_strategy_learning.json', shared_learning_path))
    files = []
    for relative, source_path in source_files:
        if not source_path.exists():
            continue
        files.append(
            {
                'relative_path': relative,
                **_export_artifact(
                    source_path=source_path,
                    output_path=export_dir / relative,
                    redact=bool(selected_profile['redact']),
                ),
            }
        )
    manifest_files = [_portable_export_record(file_info) for file_info in files]
    manifest = {
        'ok': True,
        'schema_version': 1,
        'director_id': director_id,
        'profile': selected_profile,
        'export_dir': '.',
        'created_at': utc_now(),
        'files': manifest_files,
        'file_count': len(manifest_files),
        'archive_path': archive_path.name if selected_profile['archive'] else None,
    }
    _write_json(manifest_path, manifest)
    manifest_file_internal = {
        'relative_path': 'manifest.json',
        'path': str(manifest_path),
        'source_path': None,
        'sha256': _sha256_file(manifest_path),
        'bytes': manifest_path.stat().st_size,
        'redacted': False,
    }
    manifest_file = _portable_export_record(manifest_file_internal)
    archive_files = files + [manifest_file_internal]
    archive_info = None
    if selected_profile['archive']:
        with tarfile.open(archive_path, 'w:gz') as archive_file:
            for file_info in archive_files:
                archive_file.add(Path(str(file_info['path'])), arcname=str(file_info['relative_path']), filter=_portable_tar_info)
        archive_info = {'path': str(archive_path), 'sha256': _sha256_file(archive_path), 'bytes': archive_path.stat().st_size}
    return {
        'ok': True,
        'dry_run': False,
        'director': _director_summary(loaded['director']),
        'profile': selected_profile,
        'export_dir': str(export_dir),
        'manifest_path': str(manifest_path),
        'manifest_file': manifest_file,
        'archive': archive_info,
        'files': manifest_files,
        'file_count': len(manifest_files),
        'message': 'Director runbook export bundle written.',
    }


def _load_bundle_graph(path: Path) -> dict[str, Any]:
    candidate = path.expanduser()
    graph_candidates = []
    if candidate.is_dir():
        graph_path = candidate / 'evidence_graph' / 'evidence_graph.json'
        runbook_path = candidate / 'runbook.json'
        manifest_path = candidate / 'manifest.json'
        graph_candidates.append(graph_path)
    else:
        graph_path = candidate
        runbook_path = candidate
        manifest_path = candidate
        graph_candidates.append(graph_path)
    if manifest_path.name == 'manifest.json' and manifest_path.exists():
        manifest = _read_json(manifest_path)
        local_graph = manifest_path.parent / 'evidence_graph' / 'evidence_graph.json'
        graph_candidates.append(local_graph)
        export_dir_text = str(manifest.get('export_dir') or '').strip()
        if export_dir_text and export_dir_text not in {'.', './'}:
            export_dir = Path(export_dir_text)
            if not export_dir.is_absolute():
                export_dir = manifest_path.parent / export_dir
            graph_candidates.append(export_dir / 'evidence_graph' / 'evidence_graph.json')
    if runbook_path.name == 'runbook.json' and runbook_path.exists():
        runbook = _read_json(runbook_path)
        graph_info = runbook.get('evidence_graph') if isinstance(runbook.get('evidence_graph'), dict) else {}
        graph_candidates.append(runbook_path.parent / 'evidence_graph' / 'evidence_graph.json')
        referenced = Path(str(graph_info.get('graph_path') or ''))
        if referenced.exists():
            graph_candidates.append(referenced)
    if graph_path.name != 'evidence_graph.json' and graph_path.is_dir():
        graph_candidates.append(graph_path / 'evidence_graph' / 'evidence_graph.json')
    for candidate_graph in graph_candidates:
        if candidate_graph.name != 'evidence_graph.json' and candidate_graph.is_dir():
            candidate_graph = candidate_graph / 'evidence_graph' / 'evidence_graph.json'
        if candidate_graph.exists():
            graph_path = candidate_graph
            break
    graph = _read_json(graph_path)
    if not graph:
        return {'ok': False, 'path': str(path), 'message': f'Could not find evidence graph for bundle: {path}'}
    return {'ok': True, 'path': str(path), 'graph_path': str(graph_path), 'graph': graph}


def _graph_claims(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node.get('label') or '').strip(): node
        for node in graph.get('nodes', []) or []
        if isinstance(node, dict) and node.get('kind') == 'claim' and str(node.get('label') or '').strip()
    }


def _graph_sources(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sources = {}
    for node in graph.get('nodes', []) or []:
        if not isinstance(node, dict) or node.get('kind') not in {'source', 'memory_source'}:
            continue
        url = str(node.get('url') or '').strip()
        node_id = str(node.get('id') or '').strip()
        key = node_id if url == '[redacted-url]' or not url else url
        if not key:
            key = str(node.get('label') or '').strip()
        if key:
            sources[key] = node
    return sources


def _graph_unresolved_contradictions(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node.get('label') or node.get('id') or '').strip(): node
        for node in graph.get('nodes', []) or []
        if isinstance(node, dict)
        and node.get('kind') == 'contradiction'
        and str(node.get('status') or '').lower() not in {'resolved', 'closed'}
        and str(node.get('label') or node.get('id') or '').strip()
    }


def _domain_counts(sources: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources.values():
        domain = str(source.get('domain') or '').strip()
        if domain:
            counts[domain] = counts.get(domain, 0) + 1
    return counts


def director_bundle_comparison_markdown(comparison: dict[str, Any]) -> str:
    counts = comparison.get('counts') if isinstance(comparison.get('counts'), dict) else {}
    lines = [
        '# Director Bundle Comparison',
        '',
        f"- Left: {_fmt(comparison.get('left_path'))}",
        f"- Right: {_fmt(comparison.get('right_path'))}",
        f"- New claims: {_fmt(counts.get('new_claims'))}",
        f"- Removed claims: {_fmt(counts.get('removed_claims'))}",
        f"- New sources: {_fmt(counts.get('new_sources'))}",
        f"- Removed sources: {_fmt(counts.get('removed_sources'))}",
        f"- Resolved contradictions: {_fmt(counts.get('resolved_contradictions'))}",
        f"- Remaining gaps: {_fmt(counts.get('remaining_gaps'))}",
        '',
        '## New Claims',
        '',
    ]
    for claim in comparison.get('new_claims', [])[:10]:
        lines.append(f'- {claim}')
    if not comparison.get('new_claims'):
        lines.append('- none')
    lines.extend(['', '## Remaining Gaps', ''])
    for gap in comparison.get('remaining_gaps', [])[:10]:
        lines.append(f"- {gap.get('kind')}: {gap.get('label')}")
    if not comparison.get('remaining_gaps'):
        lines.append('- none')
    lines.append('')
    return '\n'.join(lines)


def compare_director_bundles(
    root: Path,
    director_id: str,
    *,
    left: Path,
    right: Path,
    apply: bool = False,
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    left_bundle = _load_bundle_graph(left)
    if not left_bundle.get('ok'):
        return left_bundle
    right_bundle = _load_bundle_graph(right)
    if not right_bundle.get('ok'):
        return right_bundle
    left_graph = left_bundle['graph']
    right_graph = right_bundle['graph']
    left_claims = _graph_claims(left_graph)
    right_claims = _graph_claims(right_graph)
    left_sources = _graph_sources(left_graph)
    right_sources = _graph_sources(right_graph)
    left_contradictions = _graph_unresolved_contradictions(left_graph)
    right_contradictions = _graph_unresolved_contradictions(right_graph)
    left_domains = _domain_counts(left_sources)
    right_domains = _domain_counts(right_sources)
    new_claims = sorted(set(right_claims) - set(left_claims))
    removed_claims = sorted(set(left_claims) - set(right_claims))
    new_sources = sorted(set(right_sources) - set(left_sources))
    removed_sources = sorted(set(left_sources) - set(right_sources))
    resolved_contradictions = sorted(set(left_contradictions) - set(right_contradictions))
    new_unresolved = sorted(set(right_contradictions) - set(left_contradictions))
    domain_changes = [
        {'domain': domain, 'left_count': left_domains.get(domain, 0), 'right_count': right_domains.get(domain, 0)}
        for domain in sorted(set(left_domains) | set(right_domains))
        if left_domains.get(domain, 0) != right_domains.get(domain, 0)
    ]
    right_summary = summarize_director_evidence_graph(right_graph)
    remaining_gaps = []
    for item in right_summary.get('weak_evidence_claims', []) or []:
        remaining_gaps.append({'kind': 'weak_evidence_claim', 'label': item.get('label'), 'id': item.get('id')})
    for item in right_summary.get('unresolved_contradiction_chains', []) or []:
        remaining_gaps.append({'kind': 'unresolved_contradiction', 'label': item.get('label'), 'id': item.get('contradiction_id')})
    comparison_dir = _safe_director_dir(root, director_id) / 'bundle_comparisons' / utc_now().replace(':', '').replace('-', '').lower()
    comparison_path = comparison_dir / 'comparison.json'
    report_path = comparison_dir / 'comparison.md'
    comparison = {
        'ok': True,
        'dry_run': not apply,
        'director': _director_summary(loaded['director']),
        'left_path': str(left),
        'right_path': str(right),
        'left_graph_path': left_bundle.get('graph_path'),
        'right_graph_path': right_bundle.get('graph_path'),
        'new_claims': new_claims,
        'removed_claims': removed_claims,
        'new_sources': new_sources[:50],
        'removed_sources': removed_sources[:50],
        'resolved_contradictions': resolved_contradictions,
        'new_unresolved_contradictions': new_unresolved,
        'domain_coverage_changes': domain_changes,
        'remaining_gaps': remaining_gaps[:50],
        'counts': {
            'new_claims': len(new_claims),
            'removed_claims': len(removed_claims),
            'new_sources': len(new_sources),
            'removed_sources': len(removed_sources),
            'resolved_contradictions': len(resolved_contradictions),
            'new_unresolved_contradictions': len(new_unresolved),
            'domain_coverage_changes': len(domain_changes),
            'remaining_gaps': len(remaining_gaps),
        },
        'comparison_path': str(comparison_path),
        'report_path': str(report_path),
        'created_at': utc_now(),
    }
    comparison['markdown'] = director_bundle_comparison_markdown(comparison)
    if apply:
        comparison_dir.mkdir(parents=True, exist_ok=True)
        _write_json(comparison_path, {key: value for key, value in comparison.items() if key != 'markdown'})
        report_path.write_text(str(comparison['markdown']), encoding='utf-8')
    return comparison


def build_director_evidence_graph(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    apply: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    director = loaded['director']
    assessment = assess_research_director(director, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
    campaign = assessment.get('campaign') if isinstance(assessment.get('campaign'), dict) else {}
    waves = list_director_waves(root, director_id, limit=limit)
    graph_dir = _safe_director_dir(root, director_id) / 'evidence_graph'
    graph_path = graph_dir / 'evidence_graph.json'
    index_path = graph_dir / 'index.md'
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    director_node = f'director:{director_id}'
    campaign_id = str(director.get('campaign_id') or campaign.get('campaign_id') or '')
    campaign_node = f'campaign:{campaign_id}' if campaign_id else ''
    _add_node(nodes, seen_nodes, _graph_node(director_node, 'director', str(director.get('objective') or director_id), status=director.get('status')))
    if campaign_node:
        _add_node(nodes, seen_nodes, _graph_node(campaign_node, 'campaign', str(campaign.get('objective') or campaign_id), status=campaign.get('status')))
        edges.append(_graph_edge(director_node, campaign_node, 'manages'))

    memory = director.get('objective_memory') if isinstance(director.get('objective_memory'), dict) else {}
    for prior in memory.get('prior_runs', []) or []:
        if not isinstance(prior, dict) or not prior.get('run_id'):
            continue
        run_node = f"run:{prior['run_id']}"
        _add_node(nodes, seen_nodes, _graph_node(run_node, 'prior_run', str(prior.get('query') or prior['run_id']), status=prior.get('status'), match_score=prior.get('match_score')))
        edges.append(_graph_edge(director_node, run_node, 'remembers'))
    for source in memory.get('reusable_sources', []) or []:
        if not isinstance(source, dict) or not source.get('url'):
            continue
        source_node = f"memory_source:{source['url']}"
        _add_node(nodes, seen_nodes, _graph_node(source_node, 'memory_source', str(source.get('title') or source.get('domain') or source['url']), url=source.get('url'), domain=source.get('domain')))
        edges.append(_graph_edge(director_node, source_node, 'can_reuse_source'))
    for path in memory.get('avoid_paths', []) or []:
        if not isinstance(path, dict) or not path.get('url'):
            continue
        avoid_node = f"avoid:{path['url']}"
        _add_node(nodes, seen_nodes, _graph_node(avoid_node, 'avoid_path', str(path.get('domain') or path['url']), url=path.get('url'), reason=path.get('reason')))
        edges.append(_graph_edge(director_node, avoid_node, 'should_avoid'))

    step_run_ids: set[str] = set()
    for step in campaign.get('steps', []) or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get('step_id') or '')
        if not step_id:
            continue
        step_node = f'step:{step_id}'
        _add_node(nodes, seen_nodes, _graph_node(step_node, 'campaign_step', str(step.get('question') or step_id), status=step.get('status'), kind_label=step.get('kind')))
        if campaign_node:
            edges.append(_graph_edge(campaign_node, step_node, 'has_step'))
        for job_id in step.get('job_ids') or ([step.get('job_id')] if step.get('job_id') else []):
            job_node = f'job:{job_id}'
            _add_node(nodes, seen_nodes, _graph_node(job_node, 'job', str(job_id)))
            edges.append(_graph_edge(step_node, job_node, 'queued_job'))
        for run_id in step.get('run_ids') or []:
            run_id = str(run_id)
            step_run_ids.add(run_id)
            run_node = f'run:{run_id}'
            _add_node(nodes, seen_nodes, _graph_node(run_node, 'run', run_id))
            edges.append(_graph_edge(step_node, run_node, 'produced_run'))

    for job in campaign.get('jobs', []) or []:
        if not isinstance(job, dict) or not job.get('job_id'):
            continue
        job_node = f"job:{job['job_id']}"
        _add_node(nodes, seen_nodes, _graph_node(job_node, 'job', str(job.get('request_preview') or job['job_id']), status=job.get('status'), tags=job.get('tags')))
        if campaign_node:
            edges.append(_graph_edge(campaign_node, job_node, 'queued_job'))
        for run_id in job.get('run_ids') or []:
            run_id = str(run_id)
            step_run_ids.add(run_id)
            run_node = f'run:{run_id}'
            _add_node(nodes, seen_nodes, _graph_node(run_node, 'run', run_id))
            edges.append(_graph_edge(job_node, run_node, 'completed_as'))

    for run_id in sorted(step_run_ids):
        loaded_run = load_research_run(run_id, root=runs_root)
        if not loaded_run.get('ok'):
            continue
        metadata = loaded_run.get('run') if isinstance(loaded_run.get('run'), dict) else {}
        payload = loaded_run.get('payload') if isinstance(loaded_run.get('payload'), dict) else {}
        run_node = f'run:{run_id}'
        quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
        _add_node(nodes, seen_nodes, _graph_node(run_node, 'run', str(metadata.get('query') or run_id), status=metadata.get('status'), quality_score=quality.get('score'), quality_label=quality.get('label')))
        source_nodes_by_id: dict[str, str] = {}
        for source in payload.get('sources', []) or []:
            if not isinstance(source, dict):
                continue
            source_id = str(source.get('source_id') or source.get('id') or len(source_nodes_by_id) + 1)
            url = _source_url(source)
            source_node = f'source:{run_id}:{source_id}'
            source_nodes_by_id[source_id] = source_node
            _add_node(nodes, seen_nodes, _graph_node(source_node, 'source', str(source.get('title') or url or source_id), url=url, domain=_source_domain(url), source_id=source_id))
            edges.append(_graph_edge(run_node, source_node, 'read_source'))
        for claim in payload.get('claims', []) or []:
            if not isinstance(claim, dict):
                continue
            claim_id = str(claim.get('claim_id') or len(nodes))
            claim_node = f'claim:{run_id}:{claim_id}'
            _add_node(nodes, seen_nodes, _graph_node(claim_node, 'claim', str(claim.get('claim') or claim_id), confidence=claim.get('confidence')))
            edges.append(_graph_edge(run_node, claim_node, 'made_claim'))
            for source_id in _claim_source_ids(claim, 'supporting_sources'):
                if source_id in source_nodes_by_id:
                    edges.append(_graph_edge(claim_node, source_nodes_by_id[source_id], 'supported_by'))
            for source_id in _claim_source_ids(claim, 'conflicting_sources'):
                if source_id in source_nodes_by_id:
                    edges.append(_graph_edge(claim_node, source_nodes_by_id[source_id], 'conflicted_by'))
        contradiction = payload.get('contradiction_table') if isinstance(payload.get('contradiction_table'), dict) else {}
        for index, item in enumerate(contradiction.get('rows', []) or [], start=1):
            if not isinstance(item, dict):
                continue
            contradiction_node = f'contradiction:{run_id}:{index}'
            _add_node(nodes, seen_nodes, _graph_node(contradiction_node, 'contradiction', str(item.get('claim') or f'contradiction {index}'), status=item.get('status')))
            edges.append(_graph_edge(run_node, contradiction_node, 'has_contradiction'))

    for wave in waves.get('waves', []) or []:
        if not isinstance(wave, dict):
            continue
        wave_id = str(wave.get('wave_id') or Path(str(wave.get('wave_path') or '')).parent.name)
        if not wave_id:
            continue
        wave_node = f'wave:{wave_id}'
        _add_node(nodes, seen_nodes, _graph_node(wave_node, 'wave', wave_id, stop_reason=wave.get('stop_reason'), cycle_count=wave.get('cycle_count')))
        edges.append(_graph_edge(director_node, wave_node, 'ran_wave'))
        if wave.get('recovery_reviewed'):
            recovery_node = f'recovery:{wave_id}'
            _add_node(nodes, seen_nodes, _graph_node(recovery_node, 'recovery_action', wave_id, reasons=wave.get('recovery_reasons')))
            edges.append(_graph_edge(wave_node, recovery_node, 'reviewed_by_recovery'))

    synthesis = director.get('synthesis') if isinstance(director.get('synthesis'), dict) else {}
    if synthesis:
        synthesis_node = f'synthesis:{campaign_id or director_id}'
        _add_node(nodes, seen_nodes, _graph_node(synthesis_node, 'synthesis', str(synthesis.get('bundle_dir') or 'synthesis'), ok=synthesis.get('ok'), bundle_dir=synthesis.get('bundle_dir')))
        edges.append(_graph_edge(director_node, synthesis_node, 'wrote_synthesis'))
        if campaign_node:
            edges.append(_graph_edge(campaign_node, synthesis_node, 'synthesized_into'))

    nodes_by_kind: dict[str, int] = {}
    for node in nodes:
        kind = str(node.get('kind') or 'unknown')
        nodes_by_kind[kind] = nodes_by_kind.get(kind, 0) + 1
    edges_by_relation: dict[str, int] = {}
    for edge in edges:
        relation = str(edge.get('relation') or 'related')
        edges_by_relation[relation] = edges_by_relation.get(relation, 0) + 1
    counts = {
        'nodes': len(nodes),
        'edges': len(edges),
        'runs': nodes_by_kind.get('run', 0) + nodes_by_kind.get('prior_run', 0),
        'sources': nodes_by_kind.get('source', 0) + nodes_by_kind.get('memory_source', 0),
        'claims': nodes_by_kind.get('claim', 0),
        'nodes_by_kind': nodes_by_kind,
        'edges_by_relation': edges_by_relation,
    }
    graph = {
        'ok': True,
        'dry_run': not apply,
        'schema_version': 1,
        'director': _director_summary(director),
        'graph_path': str(graph_path),
        'index_path': str(index_path),
        'created_at': utc_now(),
        'counts': counts,
        'nodes': nodes,
        'edges': edges,
    }
    graph['markdown'] = director_evidence_graph_markdown(graph)
    if apply:
        graph_dir.mkdir(parents=True, exist_ok=True)
        _write_json(graph_path, {key: value for key, value in graph.items() if key != 'markdown'})
        index_path.write_text(str(graph['markdown']), encoding='utf-8')
    return graph


def recover_research_director(
    root: Path,
    director_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    worker_state_dir: Path | None = None,
    apply: bool = False,
    stale_hours: int = 24,
    policy: str = 'manual',
    cancel_stuck_jobs: bool = False,
    review_waves: bool = True,
    start_worker_enabled: bool = False,
    resume_checkpoints: bool = False,
    tmux: bool = False,
) -> dict[str, Any]:
    loaded = load_research_director(root, director_id)
    if not loaded.get('ok'):
        return loaded
    director = loaded['director']
    selected_policy = _recovery_policy(policy)
    now = datetime.now(UTC).replace(microsecond=0)
    stale_before = now - timedelta(hours=max(1, int(stale_hours)))
    waves = list_director_waves(root, director_id, limit=100)
    stale_waves = []
    failed_worker_waves = []
    reviewed_waves = []
    for wave in waves.get('waves', []) or []:
        if not isinstance(wave, dict):
            continue
        wave_time = _parse_utc(wave.get('created_at'))
        worker_start = wave.get('worker_start') if isinstance(wave.get('worker_start'), dict) else {}
        failed_worker = bool(worker_start and worker_start.get('ok') is False)
        stale = bool(wave_time and wave_time < stale_before and wave.get('stop_reason') in {'max_cycles', 'waiting_for_worker'})
        if failed_worker:
            failed_worker_waves.append(wave)
        if stale:
            stale_waves.append(wave)
        if apply and review_waves and (failed_worker or stale) and wave.get('wave_path'):
            path = Path(str(wave['wave_path']))
            payload = _read_json(path)
            payload['recovery_reviewed'] = True
            payload['recovery_reviewed_at'] = utc_now()
            reasons = list(payload.get('recovery_reasons') or [])
            if stale and 'stale_wave' not in reasons:
                reasons.append('stale_wave')
            if failed_worker and 'failed_worker_start' not in reasons:
                reasons.append('failed_worker_start')
            payload['recovery_reasons'] = reasons
            _write_json(path, payload)
            reviewed_waves.append({'wave_id': payload.get('wave_id'), 'wave_path': str(path), 'reasons': reasons})

    stuck_jobs = []
    cancelled_jobs = []
    all_jobs = []
    resolved_jobs_root = jobs_root.expanduser().resolve()
    if resolved_jobs_root.exists():
        for job_path in resolved_jobs_root.glob('*/job.json'):
            job = _read_json(job_path)
            if job:
                job.setdefault('job_path', str(job_path))
                all_jobs.append(job)
    director_tag = f'director:{director_id}'
    for job in all_jobs:
        if director_tag not in (job.get('tags') or []):
            continue
        if str(job.get('status')) not in {'queued', 'leased', 'running'}:
            continue
        updated_at = _parse_utc(job.get('updated_at') or job.get('created_at'))
        stale = bool(updated_at and updated_at < stale_before)
        if not stale:
            continue
        stuck_jobs.append(job)
        if apply and cancel_stuck_jobs and job.get('job_id'):
            cancelled_jobs.append(
                update_research_job(
                    jobs_root,
                    str(job['job_id']),
                    status='cancelled',
                    event='director_recovery_cancelled',
                    message=f'Cancelled by director recovery after {stale_hours} stale hour(s).',
                )
            )

    campaign = {}
    loaded_campaign = load_research_campaign(campaign_root, str(director.get('campaign_id') or ''))
    if loaded_campaign.get('ok'):
        campaign = summarize_campaign(loaded_campaign['campaign'], jobs_root=jobs_root, runs_root=runs_root)
    campaign_run_ids = {str(run_id) for run_id in campaign.get('run_ids', []) or []}
    checkpoints = list_research_checkpoints(status='interrupted', limit=100, root=runs_root)
    interrupted = []
    for item in checkpoints.get('checkpoints', []) or []:
        run_id = str(item.get('run_id') or '')
        if campaign_run_ids and run_id not in campaign_run_ids:
            continue
        interrupted.append(
            {
                'run_id': run_id,
                'updated_at': item.get('updated_at'),
                'query': item.get('query') or item.get('title'),
                'suggested_actions': item.get('suggested_actions') or [],
            }
        )

    worker_recovery = {'requested': bool(start_worker_enabled), 'allowed': bool(selected_policy.get('allow_worker_restart'))}
    if start_worker_enabled:
        if not selected_policy.get('allow_worker_restart'):
            worker_recovery['skipped'] = True
            worker_recovery['message'] = f"Recovery policy '{selected_policy['name']}' does not allow worker restart."
        else:
            state_dir = Path(worker_state_dir or root.parent / 'research_job_worker')
            status = status_worker(state_dir=state_dir)
            worker_recovery['status'] = status
            if status.get('running'):
                worker_recovery['started'] = False
                worker_recovery['already_running'] = True
            else:
                worker_recovery['start'] = start_worker(
                    jobs_root=jobs_root,
                    state_dir=state_dir,
                    worker_id=f'research-director-recovery-{director_id[:16]}',
                    lease_seconds=3600,
                    max_jobs=0,
                    poll_seconds=30,
                    idle_exit_seconds=0,
                    watch=True,
                    tmux=tmux,
                    session='lmstudio-research-worker',
                    dry_run=not apply,
                )
                worker_recovery['started'] = bool(worker_recovery['start'].get('started'))

    checkpoint_recovery = {
        'requested': bool(resume_checkpoints),
        'allowed': bool(selected_policy.get('allow_checkpoint_resume')),
        'resume_actions': [],
    }
    if resume_checkpoints:
        if not selected_policy.get('allow_checkpoint_resume'):
            checkpoint_recovery['skipped'] = True
            checkpoint_recovery['message'] = f"Recovery policy '{selected_policy['name']}' does not allow checkpoint resume."
        else:
            for item in interrupted:
                run_id = str(item.get('run_id') or '')
                if run_id:
                    checkpoint_recovery['resume_actions'].append(
                        {
                            'run_id': run_id,
                            'tool': 'safe_resume_deep_research',
                            'example': f'safe_resume_deep_research(run_id="{run_id}")',
                        }
                    )

    return {
        'ok': True,
        'dry_run': not apply,
        'director': _director_summary(director),
        'policy': selected_policy,
        'stale_hours': stale_hours,
        'stale_waves': stale_waves,
        'failed_worker_waves': failed_worker_waves,
        'reviewed_waves': reviewed_waves,
        'stuck_jobs': stuck_jobs,
        'cancelled_jobs': cancelled_jobs,
        'interrupted_checkpoints': interrupted,
        'worker_recovery': worker_recovery,
        'checkpoint_recovery': checkpoint_recovery,
        'issue_counts': {
            'stale_waves': len(stale_waves),
            'failed_worker_waves': len(failed_worker_waves),
            'stuck_jobs': len(stuck_jobs),
            'cancelled_jobs': len(cancelled_jobs),
            'interrupted_checkpoints': len(interrupted),
            'checkpoint_resume_actions': len(checkpoint_recovery['resume_actions']),
        },
        'message': 'Preview only. Add apply=true and explicit repair options to write recovery changes.' if not apply else 'Director recovery completed.',
    }


def research_director_command(
    request: str,
    *,
    root: Path,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    synthesis_root: Path,
    worker_state_dir: Path | None = None,
) -> dict[str, Any]:
    parsed = parse_director_request(request)
    options = parsed['options']
    values = [str(value).strip() for value in parsed['values'] if str(value).strip()]
    action = str(options.get('action') or '').strip().lower()
    apply = _bool_option(options, 'apply')
    limit = _int_option(options, 'limit', 10, minimum=1, maximum=100)
    if action in {'import_learning', 'restore_learning', 'learning_import', 'learning_restore'}:
        source = str(options.get('source') or options.get('path') or options.get('bundle') or (values[0] if values else '')).strip()
        if not source:
            return {
                'ok': False,
                'tool': 'safe_research_director',
                'message': 'Learning import requires source=<runbook export dir, archive, or learning JSON path>.',
            }
        return {
            'tool': 'safe_research_director',
            **import_remediation_strategy_learning(root, Path(source), apply=apply),
        }
    if action in {'status', 'list', 'latest'} and not values:
        result = list_research_directors(root, limit=limit)
        result['tool'] = 'safe_research_director'
        return result
    if values and action in {'', 'status', 'review'}:
        loaded = load_research_director(root, values[0])
        if not loaded.get('ok'):
            return loaded
        assessment = assess_research_director(loaded['director'], campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
        return {'ok': assessment.get('ok'), 'tool': 'safe_research_director', 'director': assessment.get('director'), 'assessment': assessment}
    if values and action in {'advance', 'synthesize', 'wave', 'autopilot', 'auto', 'autopilot_status', 'autopilots', 'autopilot_history', 'dashboard', 'history', 'graph', 'evidence_graph', 'graph_actions', 'execute_graph_actions', 'comparison_actions', 'compare_actions', 'execute_comparison_actions', 'comparison_replay', 'replay_comparison_actions', 'runbook', 'handoff', 'runbook_export', 'export_runbook', 'compare_bundles', 'compare_bundle', 'recovery', 'recover'}:
        if action in {'recovery', 'recover'}:
            policy_name = str(options.get('policy') or options.get('recovery_policy') or 'manual')
            recovery_policy = _recovery_policy(policy_name)
            return {
                'tool': 'safe_research_director',
                **recover_research_director(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    worker_state_dir=Path(str(options.get('worker_state_dir') or worker_state_dir or root.parent / 'research_job_worker')),
                    apply=apply,
                    stale_hours=_int_option(options, 'stale_hours', int(recovery_policy['stale_hours']), minimum=1, maximum=24 * 30),
                    policy=policy_name,
                    cancel_stuck_jobs=_bool_option(
                        options,
                        'cancel_stuck_jobs',
                        default=bool(recovery_policy.get('cancel_stuck_jobs')),
                    ),
                    review_waves=_bool_option(options, 'review_waves', default=bool(recovery_policy.get('review_waves'))),
                    start_worker_enabled=_bool_option(options, 'start_worker', default=bool(recovery_policy.get('allow_worker_restart'))),
                    resume_checkpoints=_bool_option(options, 'resume_checkpoints', default=bool(recovery_policy.get('allow_checkpoint_resume'))),
                    tmux=_bool_option(options, 'tmux'),
                ),
            }
        if action in {'dashboard', 'history'}:
            return {
                'tool': 'safe_research_director',
                **build_research_director_dashboard(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    apply=apply,
                    limit=limit,
                ),
            }
        if action in {'autopilots', 'autopilot_history'}:
            return {
                'tool': 'safe_research_director',
                **list_director_autopilots(root, values[0], limit=limit),
            }
        if action == 'autopilot_status':
            return {
                'tool': 'safe_research_director',
                **load_director_autopilot(
                    root,
                    values[0],
                    autopilot_id=str(options.get('autopilot_id') or options.get('id') or '').strip() or None,
                ),
            }
        if action in {'graph', 'evidence_graph'}:
            return {
                'tool': 'safe_research_director',
                **build_director_evidence_graph(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    apply=apply,
                    limit=limit,
                ),
            }
        if action in {'graph_actions', 'execute_graph_actions'}:
            return {
                'tool': 'safe_research_director',
                **execute_director_graph_actions(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    apply=apply,
                    selected_action=str(options.get('graph_action') or options.get('graph_recommendation') or 'all'),
                    max_actions=_int_option(options, 'max_actions', 3, minimum=0, maximum=20),
                ),
            }
        if action in {'runbook', 'handoff'}:
            return {
                'tool': 'safe_research_director',
                **build_director_runbook(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    apply=apply,
                    limit=limit,
                ),
            }
        if action in {'runbook_export', 'export_runbook'}:
            return {
                'tool': 'safe_research_director',
                **export_director_runbook(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    apply=apply,
                    profile=str(options.get('profile') or 'full-fidelity'),
                    redact=_bool_option(options, 'redact') if 'redact' in options else None,
                    archive=_bool_option(options, 'archive') if 'archive' in options else None,
                    limit=limit,
                ),
            }
        if action in {'compare_bundles', 'compare_bundle'}:
            left = str(options.get('left') or options.get('before') or '').strip()
            right = str(options.get('right') or options.get('after') or '').strip()
            if not left or not right:
                return {'ok': False, 'tool': 'safe_research_director', 'message': 'Bundle comparison requires left=<path> and right=<path>.'}
            return {
                'tool': 'safe_research_director',
                **compare_director_bundles(
                    root,
                    values[0],
                    left=Path(left),
                    right=Path(right),
                    apply=apply,
                ),
            }
        if action in {'comparison_actions', 'compare_actions', 'execute_comparison_actions'}:
            left = str(options.get('left') or options.get('before') or '').strip()
            right = str(options.get('right') or options.get('after') or '').strip()
            if not left or not right:
                return {'ok': False, 'tool': 'safe_research_director', 'message': 'Comparison actions require left=<path> and right=<path>.'}
            return {
                'tool': 'safe_research_director',
                **execute_director_comparison_actions(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    left=Path(left),
                    right=Path(right),
                    apply=apply,
                    selected_action=str(options.get('comparison_action') or options.get('compare_action') or 'all'),
                    max_actions=_int_option(options, 'max_actions', 3, minimum=0, maximum=20),
                ),
            }
        if action in {'comparison_replay', 'replay_comparison_actions'}:
            return {
                'tool': 'safe_research_director',
                **replay_director_comparison_actions(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    event_id=str(options.get('event_id') or options.get('comparison_event') or '').strip() or None,
                    apply=apply,
                    selected_action=str(options.get('comparison_action') or options.get('compare_action') or 'all'),
                    max_actions=_int_option(options, 'max_actions', 3, minimum=0, maximum=20),
                    replay_reason=str(options.get('reason') or options.get('replay_reason') or 'failed_or_no_evidence_followup'),
                ),
            }
        if action == 'wave':
            return {
                'tool': 'safe_research_director',
                **run_research_director_wave(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    synthesis_root=synthesis_root,
                    worker_state_dir=Path(str(options.get('worker_state_dir') or worker_state_dir or root.parent / 'research_job_worker')),
                    apply=apply,
                    start_worker_enabled=_bool_option(options, 'start_worker'),
                    max_cycles=_int_option(options, 'max_cycles', 3, minimum=1, maximum=50),
                    max_followups=_int_option(options, 'max_followups', 3, minimum=0, maximum=20),
                    local_synthesis=_bool_option(options, 'local_synthesis'),
                    worker_id=str(options.get('worker_id') or 'research-director-wave'),
                    tmux=_bool_option(options, 'tmux'),
                    session=str(options.get('session') or 'lmstudio-research-worker'),
                ),
            }
        if action in {'autopilot', 'auto'}:
            return {
                'tool': 'safe_research_director',
                **run_research_director_autopilot(
                    root,
                    values[0],
                    campaign_root=campaign_root,
                    jobs_root=jobs_root,
                    runs_root=runs_root,
                    synthesis_root=synthesis_root,
                    worker_state_dir=Path(str(options.get('worker_state_dir') or worker_state_dir or root.parent / 'research_job_worker')),
                    apply=apply,
                    start_worker_enabled=_bool_option(options, 'start_worker'),
                    run_worker_enabled=_bool_option(options, 'run_worker'),
                    max_iterations=_int_option(options, 'max_iterations', 5, minimum=1, maximum=50),
                    max_cycles_per_iteration=_int_option(options, 'max_cycles', 2, minimum=1, maximum=20),
                    max_followups=_int_option(options, 'max_followups', 3, minimum=0, maximum=20),
                    worker_jobs_per_iteration=_int_option(options, 'worker_jobs_per_iteration', 1, minimum=0, maximum=25),
                    local_synthesis=_bool_option(options, 'local_synthesis'),
                    recovery_policy=str(options.get('recovery_policy') or options.get('policy') or 'none'),
                    auto_recover=_bool_option(options, 'auto_recover'),
                    write_dashboard=_bool_option(options, 'write_dashboard', default=True),
                    write_runbook_on_stop=_bool_option(options, 'write_runbook', default=True),
                    worker_id=str(options.get('worker_id') or 'research-director-autopilot'),
                    tmux=_bool_option(options, 'tmux'),
                    session=str(options.get('session') or 'lmstudio-research-worker'),
                ),
            }
        return {
            'tool': 'safe_research_director',
            **advance_research_director(
                root,
                values[0],
                campaign_root=campaign_root,
                jobs_root=jobs_root,
                runs_root=runs_root,
                synthesis_root=synthesis_root,
                apply=apply,
                synthesize=action == 'synthesize' or _bool_option(options, 'synthesize'),
                local_synthesis=_bool_option(options, 'local_synthesis'),
                max_followups=_int_option(options, 'max_followups', 3, minimum=0, maximum=20),
            ),
        }
    objective = parsed.get('objective') or ''
    if not objective:
        return {'ok': False, 'tool': 'safe_research_director', 'message': 'Research director needs an objective, director_id, or status/list action.'}
    profile = str(options.get('profile') or 'careful')
    depth = str(options.get('depth') or 'deep')
    priority = _int_option(options, 'priority', 0, minimum=-100, maximum=100)
    budget_jobs = _int_option(options, 'budget_jobs', 12, minimum=1, maximum=100)
    quality_target = str(options.get('quality_target') or 'strong')
    if not apply:
        get_work_profile(profile)
        depth = normalize_campaign_depth(depth)
        steps = plan_campaign_questions(str(objective), depth=depth)
        objective_memory = build_director_objective_memory(str(objective), runs_root=runs_root, limit=5)
        return {
            'ok': True,
            'tool': 'safe_research_director',
            'dry_run': True,
            'planned_director': {
                'objective': objective,
                'profile': profile,
                'depth': depth,
                'budget_jobs': budget_jobs,
                'quality_target': quality_target,
                'quality_target_score': _quality_target_score(quality_target),
                'initial_step_count': len(steps),
                'steps': steps,
                'objective_memory': objective_memory,
            },
            'message': 'Preview only. Add apply=true to create the director and queued campaign.',
        }
    result = create_research_director(
        root,
        objective=str(objective),
        campaign_root=campaign_root,
        jobs_root=jobs_root,
        runs_root=runs_root,
        profile=profile,
        depth=depth,
        budget_jobs=budget_jobs,
        quality_target=quality_target,
        priority=priority,
        queue=True,
    )
    result['tool'] = 'safe_research_director'
    result['dry_run'] = False
    return result
