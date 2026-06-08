from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from web_research.config import settings


RUN_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,120}$')
TOKEN_RE = re.compile(r'[a-z0-9]{3,}')


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _slug(value: str, *, limit: int = 42) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return slug[:limit].strip('-') or 'research'


def _tokens(value: str) -> set[str]:
    stopwords = {
        'about',
        'after',
        'against',
        'and',
        'are',
        'best',
        'can',
        'compare',
        'current',
        'for',
        'from',
        'how',
        'into',
        'latest',
        'local',
        'research',
        'should',
        'source',
        'the',
        'this',
        'tools',
        'use',
        'using',
        'what',
        'when',
        'with',
    }
    return {token for token in TOKEN_RE.findall(value.lower()) if token not in stopwords}


def _short_answer(payload: dict[str, Any]) -> str:
    claims = payload.get('claims', []) or []
    if claims and isinstance(claims[0], dict) and claims[0].get('claim'):
        return str(claims[0]['claim'])[:240]
    message = str(payload.get('message') or '').strip()
    if message:
        return message[:240]
    report = str(payload.get('final_report') or '').strip()
    for line in report.splitlines():
        stripped = line.strip(' #-')
        if stripped and not stripped.lower().startswith(('executive brief', 'research run')):
            return stripped[:240]
    return ''


def run_budget_summary(payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload.get('sources', []) if isinstance(payload.get('sources'), list) else []
    evidence = payload.get('evidence', []) if isinstance(payload.get('evidence'), list) else []
    failures = payload.get('failures', []) if isinstance(payload.get('failures'), list) else []
    blocked_sources = payload.get('blocked_sources', []) if isinstance(payload.get('blocked_sources'), list) else []
    searches = payload.get('searches', []) if isinstance(payload.get('searches'), list) else []
    selection_trace = payload.get('selection_trace', []) if isinstance(payload.get('selection_trace'), list) else []
    strategy = payload.get('strategy') if isinstance(payload.get('strategy'), dict) else {}
    agent_loop = payload.get('agent_loop') if isinstance(payload.get('agent_loop'), dict) else {}
    source_selection = payload.get('source_selection_telemetry') if isinstance(payload.get('source_selection_telemetry'), dict) else {}

    rendered_sources = sum(1 for source in sources if isinstance(source, dict) and source.get('rendered'))
    recovered_sources = sum(1 for source in sources if isinstance(source, dict) and source.get('recovered_from'))
    browser_interacted_sources = sum(
        1 for source in sources if isinstance(source, dict) and source.get('browser_interactions')
    )
    selected_trace_count = sum(
        1 for item in selection_trace if isinstance(item, dict) and item.get('decision') in {'selected', 'selected_recovery'}
    )
    follow_up_searches = [
        item
        for item in searches
        if isinstance(item, dict) and str(item.get('intent') or '').lower() in {'gap_follow_up', 'follow_up'}
    ]
    agent_rounds = agent_loop.get('rounds') if isinstance(agent_loop.get('rounds'), list) else []

    return {
        'source_count': len(sources),
        'evidence_count': len(evidence),
        'failure_count': len(failures),
        'blocked_source_count': len(blocked_sources),
        'rendered_source_count': rendered_sources,
        'browser_interacted_source_count': browser_interacted_sources,
        'recovered_source_count': recovered_sources,
        'search_count': len(searches),
        'follow_up_search_count': len(follow_up_searches),
        'planned_follow_up_count': len(strategy.get('auto_follow_up_plan', []) or [])
        if isinstance(strategy.get('auto_follow_up_plan'), list)
        else 0,
        'follow_up_rounds_requested': int(strategy.get('follow_up_rounds') or 0)
        if str(strategy.get('follow_up_rounds') or '').isdigit()
        else 0,
        'agent_round_count': len(agent_rounds),
        'selection_trace_count': len(selection_trace),
        'selected_trace_count': selected_trace_count,
        'cache_hit_source_count': sum(1 for source in sources if isinstance(source, dict) and source.get('cached')),
        'planned_authority_source_count': int(source_selection.get('planned_authority_source_count') or 0),
        'selected_authority_source_count': int(source_selection.get('selected_authority_source_count') or 0),
        'planned_low_value_source_count': int(source_selection.get('planned_low_value_source_count') or 0),
        'planned_policy_skip_count': int(source_selection.get('planned_policy_skip_count') or 0),
        'repeated_domain_count': int(source_selection.get('repeated_domain_count') or 0),
    }


def _json_default(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _safe_run_dir(run_id: str, *, root: Path | None = None) -> Path:
    if not RUN_ID_RE.match(run_id):
        raise ValueError('Invalid research run id')
    base = root or settings.research_runs_dir
    run_dir = (base / run_id).resolve()
    base_resolved = base.resolve()
    if base_resolved not in run_dir.parents and run_dir != base_resolved:
        raise ValueError('Invalid research run path')
    return run_dir


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(f'{path.suffix}.tmp')
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')
    temp_path.replace(path)


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    metadata = run.get('run') if isinstance(run.get('run'), dict) else {}
    payload = run.get('payload') if isinstance(run.get('payload'), dict) else {}
    run_id = metadata.get('run_id')
    kind = metadata.get('kind')
    status = metadata.get('status')
    suggested_actions = []
    if run_id and kind == 'deep_research' and status in {'in_progress', 'interrupted'}:
        suggested_actions.append(
            {
                'tool': 'safe_resume_deep_research',
                'reason': 'Run is a resumable deep_research checkpoint.',
                'example': f'safe_resume_deep_research(run_id="{run_id}")',
            }
        )
    if run_id and status == 'completed':
        suggested_actions.append(
            {
                'tool': 'safe_continue_research_run',
                'reason': 'Run is completed and can be extended with a follow-up query.',
                'example': f'safe_continue_research_run(request="{run_id}\\n<follow-up query>")',
            }
        )
        suggested_actions.append(
            {
                'tool': 'safe_export_research_run',
                'reason': 'Run is completed and can be exported as a review/share bundle.',
                'example': f'safe_export_research_run(request="{run_id}\\nprofile=private-share")',
            }
        )
        suggested_actions.append(
            {
                'tool': 'safe_build_source_pack',
                'reason': 'Run is completed and can be included in a redacted source handoff pack.',
                'example': f'safe_build_source_pack(request="{run_id}")',
            }
        )
    return {
        'run_id': run_id,
        'kind': kind,
        'status': status,
        'created_at': metadata.get('created_at'),
        'updated_at': metadata.get('updated_at'),
        'query': metadata.get('query'),
        'title': str(payload.get('question') or payload.get('query') or metadata.get('query') or '')[:120],
        'short_answer': _short_answer(payload),
        'parent_run_id': metadata.get('parent_run_id'),
        'source_count': len(payload.get('sources', []) or []),
        'evidence_count': len(payload.get('evidence', []) or []),
        'claim_count': len(payload.get('claims', []) or []),
        'budget': payload.get('budget') if isinstance(payload.get('budget'), dict) else run_budget_summary(payload),
        'research_quality': payload.get('research_quality'),
        'source_quality': payload.get('source_quality'),
        'recommended_next_searches': list(payload.get('recommended_next_searches', []) or [])[:3],
        'has_final_report': bool(payload.get('final_report')),
        'final_report_path': payload.get('final_report_path'),
        'checkpoint': payload.get('checkpoint'),
        'suggested_actions': suggested_actions,
        'ok': payload.get('ok'),
        'message': payload.get('message'),
    }


def save_research_run(
    kind: str,
    query: str,
    payload: dict[str, Any],
    *,
    parent_run_id: str | None = None,
    status: str = 'completed',
    root: Path | None = None,
) -> dict[str, Any]:
    base = root or settings.research_runs_dir
    base.mkdir(parents=True, exist_ok=True)
    created_at = _utc_now()
    nonce = uuid.uuid4().hex
    digest = hashlib.sha256(f'{kind}\0{query}\0{created_at}\0{nonce}'.encode('utf-8')).hexdigest()[:10]
    base_run_id = f'{created_at.replace(":", "").replace("-", "")}-{_slug(query)}-{digest}'.lower()
    run_id = base_run_id
    suffix = 2
    while (base / run_id).exists():
        run_id = f'{base_run_id}-{suffix}'
        suffix += 1
    run_dir = _safe_run_dir(run_id, root=base)
    run_dir.mkdir(parents=False, exist_ok=False)
    metadata = {
        'run_id': run_id,
        'kind': kind,
        'query': query,
        'status': status,
        'created_at': created_at,
        'updated_at': created_at,
        'schema_version': 1,
    }
    if parent_run_id:
        metadata['parent_run_id'] = parent_run_id
    stored_payload = dict(payload)
    stored_payload['run_id'] = run_id
    stored_payload['run_path'] = str(run_dir / 'run.json')
    stored_payload['budget'] = run_budget_summary(stored_payload)
    final_report = stored_payload.get('final_report')
    if isinstance(final_report, str) and final_report.strip():
        report_path = run_dir / 'report.md'
        report_path.write_text(final_report, encoding='utf-8')
        stored_payload['final_report_path'] = str(report_path)
    run = {'run': metadata, 'payload': stored_payload}
    final_path = run_dir / 'run.json'
    _atomic_write_json(final_path, run)
    summary = _run_summary(run)
    _atomic_write_json(run_dir / 'summary.json', summary)
    return {
        'saved': True,
        'run_id': run_id,
        'run_path': str(final_path),
        'final_report_path': stored_payload.get('final_report_path'),
        'created_at': created_at,
    }


def update_research_run(run_id: str, payload: dict[str, Any], *, status: str | None = None, root: Path | None = None) -> dict[str, Any]:
    run_path = _safe_run_dir(run_id, root=root) / 'run.json'
    if not run_path.exists():
        return {'ok': False, 'message': f'Research run not found: {run_id}', 'run_id': run_id}
    try:
        run = json.loads(run_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return {'ok': False, 'message': f'Could not load research run: {exc}', 'run_id': run_id}
    if not isinstance(run, dict) or not isinstance(run.get('run'), dict):
        return {'ok': False, 'message': 'Research run file is missing metadata', 'run_id': run_id}
    metadata = dict(run['run'])
    metadata['updated_at'] = _utc_now()
    if status:
        metadata['status'] = status
    run_dir = run_path.parent
    stored_payload = dict(payload)
    stored_payload['run_id'] = run_id
    stored_payload['run_path'] = str(run_path)
    stored_payload['budget'] = run_budget_summary(stored_payload)
    final_report = stored_payload.get('final_report')
    if isinstance(final_report, str) and final_report.strip():
        report_path = run_dir / 'report.md'
        report_path.write_text(final_report, encoding='utf-8')
        stored_payload['final_report_path'] = str(report_path)
    updated = {'run': metadata, 'payload': stored_payload}
    _atomic_write_json(run_path, updated)
    _atomic_write_json(run_dir / 'summary.json', _run_summary(updated))
    return {
        'ok': True,
        'run_id': run_id,
        'run_path': str(run_path),
        'final_report_path': stored_payload.get('final_report_path'),
        'updated_at': metadata['updated_at'],
    }


def list_research_checkpoints(
    *,
    status: str | None = None,
    limit: int = 20,
    root: Path | None = None,
) -> dict[str, Any]:
    wanted_status = str(status or '').strip() or None
    listed = list_research_runs(limit=100, root=root)
    if not listed.get('ok'):
        return listed
    checkpoints = []
    for summary in listed.get('runs', []) or []:
        if summary.get('kind') != 'deep_research':
            continue
        if summary.get('status') not in {'in_progress', 'interrupted'}:
            continue
        if wanted_status and summary.get('status') != wanted_status:
            continue
        checkpoints.append(summary)
    limit = max(1, min(limit, 100))
    return {
        'ok': True,
        'checkpoints': checkpoints[:limit],
        'count': len(checkpoints[:limit]),
        'total_count': len(checkpoints),
    }


def interrupt_research_checkpoint(
    run_id: str,
    *,
    root: Path | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    loaded = load_research_run(run_id, root=root)
    if not loaded.get('ok'):
        return loaded
    metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
    payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
    if metadata.get('kind') != 'deep_research':
        return {'ok': False, 'run_id': run_id, 'message': 'Only deep_research checkpoints can be interrupted.'}
    if metadata.get('status') == 'completed':
        return {'ok': False, 'run_id': run_id, 'message': 'Completed research runs cannot be interrupted.'}
    if metadata.get('status') == 'interrupted':
        return {'ok': True, 'run_id': run_id, 'already_interrupted': True, 'message': 'Research checkpoint is already interrupted.'}
    if metadata.get('status') != 'in_progress':
        return {'ok': False, 'run_id': run_id, 'message': f"Research run status is not interruptible: {metadata.get('status')}"}
    updated_payload = dict(payload)
    updated_payload['interruption'] = {
        'interrupted_at': _utc_now(),
        'reason': message or 'Marked interrupted by checkpoint cleanup.',
        'resume_supported': True,
    }
    result = update_research_run(run_id, updated_payload, status='interrupted', root=root)
    if not result.get('ok'):
        return result
    return {
        **result,
        'already_interrupted': False,
        'message': 'Research checkpoint marked interrupted. Resume data was preserved.',
    }


def load_research_run(run_id: str, *, root: Path | None = None) -> dict[str, Any]:
    try:
        run_path = _safe_run_dir(run_id, root=root) / 'run.json'
    except ValueError:
        return {'ok': False, 'message': 'Invalid research run id', 'run_id': run_id}
    if not run_path.exists():
        return {'ok': False, 'message': f'Research run not found: {run_id}', 'run_id': run_id}
    try:
        run = json.loads(run_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return {'ok': False, 'message': f'Could not load research run: {exc}', 'run_id': run_id}
    if not isinstance(run, dict):
        return {'ok': False, 'message': 'Research run file is not a JSON object', 'run_id': run_id}
    return {'ok': True, **run}


def list_research_runs(*, limit: int = 20, root: Path | None = None) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    base = root or settings.research_runs_dir
    if not base.exists():
        return {'ok': True, 'runs': [], 'count': 0}
    runs = []
    seen_run_ids: set[str] = set()
    for summary_path in base.glob('*/summary.json'):
        try:
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(summary, dict):
            if summary.get('run_id'):
                seen_run_ids.add(str(summary['run_id']))
            runs.append(summary)
    for run_path in base.glob('*/run.json'):
        run_id = run_path.parent.name
        if run_id in seen_run_ids:
            continue
        try:
            run = json.loads(run_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(run, dict):
            runs.append(_run_summary(run))
    runs.sort(key=lambda item: str(item.get('created_at') or ''), reverse=True)
    return {'ok': True, 'runs': runs[:limit], 'count': len(runs[:limit]), 'total_count': len(runs)}


def find_research_runs(query: str, *, limit: int = 5, root: Path | None = None) -> dict[str, Any]:
    limit = max(1, min(limit, 20))
    query_tokens = _tokens(query)
    listed = list_research_runs(limit=100, root=root)
    if not listed.get('ok'):
        return listed
    matches = []
    for index, summary in enumerate(listed.get('runs', []) or []):
        haystack = ' '.join(
            str(summary.get(key) or '')
            for key in ('query', 'title', 'short_answer', 'message')
        )
        haystack_tokens = _tokens(haystack)
        overlap = sorted(query_tokens & haystack_tokens)
        quality = summary.get('research_quality') if isinstance(summary.get('research_quality'), dict) else {}
        score = (len(overlap) * 10) + max(0, 5 - index)
        if quality.get('label') == 'strong':
            score += 3
        elif quality.get('label') == 'moderate':
            score += 1
        if summary.get('status') == 'in_progress':
            score += 2
        if not query_tokens:
            score = max(1, 5 - index)
        if score <= 0:
            continue
        match = dict(summary)
        match['match_score'] = score
        match['matched_terms'] = overlap
        matches.append(match)
    matches.sort(
        key=lambda item: (int(item.get('match_score') or 0), str(item.get('created_at') or '')),
        reverse=True,
    )
    return {'ok': True, 'query': query, 'runs': matches[:limit], 'count': len(matches[:limit]), 'total_count': len(matches)}


def _excerpt_text(value: Any, *, limit: int = 1200) -> str:
    text = str(value or '').strip()
    if len(text) <= limit:
        return text
    return f'{text[:limit].rstrip()}...'


def _compact_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        'source_id': source.get('source_id'),
        'title': str(source.get('title') or '')[:180],
        'url': source.get('final_url') or source.get('url'),
        'provider': source.get('provider'),
        'domain': source.get('domain'),
    }


def _compact_claim(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        'claim_id': claim.get('claim_id'),
        'claim': str(claim.get('claim') or '')[:260],
        'confidence': claim.get('confidence'),
    }


def _compact_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        'source_id': item.get('source_id'),
        'citation': item.get('citation'),
        'text': str(item.get('text') or item.get('quote') or '')[:320],
    }


def _continuation_hint(summary: dict[str, Any], query: str) -> dict[str, Any]:
    run_id = str(summary.get('run_id') or '')
    kind = summary.get('kind')
    status = summary.get('status')
    if run_id and kind == 'deep_research' and status in {'in_progress', 'interrupted'}:
        return {
            'tool': 'safe_resume_deep_research',
            'request': run_id,
            'reason': 'The matching prior run is an unfinished deep_research checkpoint.',
        }
    if run_id:
        return {
            'tool': 'safe_continue_research_run',
            'request': f'{run_id}\n{query}',
            'reason': 'The matching prior run can be extended with the current follow-up.',
        }
    return {
        'tool': 'safe_deep_research',
        'request': query,
        'reason': 'No usable prior run ID was available.',
    }


def build_research_context(query: str, *, limit: int = 3, root: Path | None = None) -> dict[str, Any]:
    """Build a compact prior-run context packet for fresh-chat continuation."""
    query = str(query or '').strip()
    if not query:
        return {
            'ok': False,
            'tool': 'safe_research_context',
            'matched': False,
            'message': 'Provide the current topic or follow-up request.',
            'expected_format': 'One text parameter containing the current research topic or follow-up request.',
        }

    found = find_research_runs(query, limit=limit, root=root)
    if not found.get('ok'):
        return {'tool': 'safe_research_context', **found}
    matches = [item for item in found.get('runs', []) or [] if isinstance(item, dict)]
    if not matches:
        return {
            'ok': True,
            'tool': 'safe_research_context',
            'matched': False,
            'query': query,
            'matches': [],
            'next_tool': {
                'tool': 'safe_deep_research',
                'request': query,
                'reason': 'No prior saved run matched this topic.',
            },
            'context_prompt': (
                'No prior saved research run matched this request. Start fresh with safe_deep_research '
                'only if the user explicitly wants current/source-backed research.'
            ),
        }

    selected = matches[0]
    run_id = str(selected.get('run_id') or '')
    loaded = load_research_run(run_id, root=root) if run_id else {'ok': False, 'message': 'Missing run_id'}
    payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
    metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
    sources = [source for source in payload.get('sources', []) or [] if isinstance(source, dict)]
    claims = [claim for claim in payload.get('claims', []) or [] if isinstance(claim, dict)]
    evidence = [item for item in payload.get('evidence', []) or [] if isinstance(item, dict)]
    summary = _run_summary(loaded) if loaded.get('ok') else selected
    next_tool = _continuation_hint(summary, query)
    final_report = payload.get('final_report') or ''
    report_excerpt = _excerpt_text(final_report, limit=1400)
    if not report_excerpt:
        report_excerpt = _short_answer(payload)

    context_prompt = '\n'.join(
        part
        for part in [
            f'Prior research context selected for current request: {query}',
            f'Run ID: {run_id}',
            f'Original topic: {summary.get("query") or summary.get("title")}',
            f'Status: {summary.get("status")} / kind: {summary.get("kind")}',
            f'Quality: {summary.get("research_quality")}',
            f'Known source/evidence/claim counts: {summary.get("source_count")}/{summary.get("evidence_count")}/{summary.get("claim_count")}',
            f'Short report excerpt: {report_excerpt}' if report_excerpt else '',
            f'Next safe tool: {next_tool["tool"]}',
            f'Next safe request:\n{next_tool["request"]}',
            'Use this prior context to avoid repeating covered searches. Continue from gaps, newer developments, contradictions, or the user follow-up.',
        ]
        if part
    )

    return {
        'ok': bool(loaded.get('ok')),
        'tool': 'safe_research_context',
        'matched': bool(loaded.get('ok')),
        'query': query,
        'selected_run_id': run_id,
        'selected_match_score': selected.get('match_score'),
        'matched_terms': selected.get('matched_terms', []),
        'matches': matches,
        'run': metadata,
        'summary': summary,
        'top_sources': [_compact_source(source) for source in sources[:8]],
        'top_claims': [_compact_claim(claim) for claim in claims[:8]],
        'top_evidence': [_compact_evidence(item) for item in evidence[:6]],
        'recommended_next_searches': list(payload.get('recommended_next_searches', []) or [])[:5],
        'report_excerpt': report_excerpt,
        'next_tool': next_tool,
        'context_prompt': context_prompt,
        'message': 'Loaded compact prior research context.' if loaded.get('ok') else loaded.get('message'),
    }
