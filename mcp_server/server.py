from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from scripts.build_source_pack import collect_source_pack, write_source_pack
from scripts.cleanup_work_loops import cleanup_stale_work_loops
from scripts.export_research_run import export_research_run, export_research_runs, select_research_run_ids
from scripts.work_dashboard import collect_work_loops
from web_research.cache import cache
from web_research.campaign_synthesis import apply_campaign_narrative_synthesis, build_campaign_synthesis, write_campaign_synthesis_bundle
from web_research.campaigns import create_research_campaign, list_research_campaigns, load_research_campaign, normalize_campaign_depth, parse_campaign_request, plan_campaign_questions, summarize_campaign
from web_research.claims import extract_claims_from_evidence, recent_change_notes, uncertainty_notes
from web_research.claim_support import build_claim_support_table
from web_research.citations import audit_citations
from web_research.compact import compact_read_payload, compact_research_payload
from web_research.config import settings
from web_research.coverage import build_research_coverage
from web_research.director import research_director_command
from web_research.evidence_index import build_evidence_index
from web_research.fetch import discover_links as run_discover_links
from web_research.fetch import read_url as run_read_url
from web_research.freshness import build_freshness_summary
from web_research.jobs import create_research_job, list_research_jobs, load_research_job, update_research_job
from web_research.local_llm import review_claim_contradictions
from web_research.mission_runtime import research_mission_runtime
from web_research.planner import QueryPlanItem, build_query_plan, classify_topic
from web_research.profiles import get_work_profile
from web_research.report import assess_research_quality, finalize_report_payload, normalize_report_format, recommended_next_searches, validate_citations
from web_research.remediation import build_research_remediation_plan
from web_research.review import adversarial_final_answer_review
from web_research.runs import build_research_context, interrupt_research_checkpoint, list_research_checkpoints
from web_research.runs import list_research_runs as run_list_research_runs
from web_research.runs import find_research_runs as run_find_research_runs
from web_research.runs import load_research_run, save_research_run, update_research_run
from web_research.search import web_search as run_web_search
from web_research.service import _source_quality_summary
from web_research.service import build_source_selection_telemetry
from web_research.service import research_web as run_research_web

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
MCP_EXPORT_ROOT = ROOT / '.runtime' / 'mcp_exports'
MCP_SOURCE_PACK_ROOT = ROOT / '.runtime' / 'mcp_source_packs'
MCP_CAMPAIGN_SYNTHESIS_ROOT = ROOT / '.runtime' / 'mcp_campaign_syntheses'
MCP_WORK_LOOP_ROOT = ROOT / '.runtime' / 'work_loops'
MCP_RESEARCH_JOBS_ROOT = ROOT / '.runtime' / 'research_jobs'
MCP_RESEARCH_CAMPAIGN_ROOT = ROOT / '.runtime' / 'research_campaigns'
MCP_RESEARCH_DIRECTOR_ROOT = ROOT / '.runtime' / 'research_directors'
MCP_DIRECTOR_SYNTHESIS_ROOT = ROOT / '.runtime' / 'director_syntheses'
MCP_RESEARCH_WORKER_STATE_DIR = ROOT / '.runtime' / 'research_job_worker'
MCP_RESEARCH_RUNS_ROOT = settings.research_runs_dir

settings.validate()

ADVANCED_TOOL_NAMES = {
    'web_search',
    'read_url',
    'discover_links',
    'research_web',
    'deep_research',
    'list_research_runs',
    'find_research_runs',
    'get_research_run',
    'invalidate_research_cache',
    'resume_deep_research',
    'continue_research_run',
}
CORE_TOOL_NAMES = {
    'safe_research_agent',
    'safe_repair_tool_call',
    'safe_web_search',
    'safe_read_url',
    'safe_research',
    'safe_deep_research',
    'safe_research_runtime',
    'safe_research_director',
}
AGENT_TOOL_NAMES = {
    'safe_research_agent',
    'safe_repair_tool_call',
}
AGENT_STRICT_TOOL_NAMES = {
    'safe_research_agent',
}

mcp = FastMCP(
    'lmstudio-web-research',
    host=settings.mcp_host,
    port=settings.mcp_port,
    mount_path=settings.mcp_mount_path,
    sse_path=settings.mcp_sse_path,
    message_path=settings.mcp_message_path,
    streamable_http_path=settings.mcp_streamable_http_path,
)


def _registered_tool_names() -> list[str]:
    tool_manager = getattr(mcp, '_tool_manager', None)
    tools = getattr(tool_manager, '_tools', {}) if tool_manager else {}
    return list(tools)


def _active_tool_profile_names() -> set[str] | None:
    if settings.mcp_tool_profile == 'agent_strict':
        return AGENT_STRICT_TOOL_NAMES
    if settings.mcp_tool_profile == 'agent':
        return AGENT_TOOL_NAMES
    if settings.mcp_tool_profile == 'core':
        return CORE_TOOL_NAMES
    return None


def _prune_mcp_tools() -> None:
    active_tool_names = _active_tool_profile_names()
    if active_tool_names is not None:
        for tool_name in _registered_tool_names():
            if tool_name not in active_tool_names:
                try:
                    mcp.remove_tool(tool_name)
                except KeyError:
                    continue
        return
    if settings.mcp_expose_advanced_tools:
        return
    for tool_name in sorted(ADVANCED_TOOL_NAMES):
        try:
            mcp.remove_tool(tool_name)
        except KeyError:
            continue


def _plan_key(query: str | None, site: str | None = None, intent: str | None = None) -> tuple[str, str, str]:
    return (str(query or ''), str(site or ''), str(intent or ''))


def _load_saved_query_plan(payload: dict | None) -> list[QueryPlanItem] | None:
    strategy = payload.get('strategy') if isinstance(payload, dict) and isinstance(payload.get('strategy'), dict) else {}
    items = strategy.get('query_plan') if isinstance(strategy, dict) else None
    if not isinstance(items, list) or not items:
        return None
    plan = []
    for item in items:
        if not isinstance(item, dict) or not item.get('query'):
            return None
        plan.append(
            QueryPlanItem(
                query=str(item['query']),
                intent=str(item.get('intent') or 'resumed'),
                rationale=str(item.get('rationale') or 'Resumed from saved query plan.'),
                site=item.get('site'),
            )
        )
    return plan


def _load_saved_auto_follow_up_plan(payload: dict | None) -> list[QueryPlanItem]:
    strategy = payload.get('strategy') if isinstance(payload, dict) and isinstance(payload.get('strategy'), dict) else {}
    items = strategy.get('auto_follow_up_plan') if isinstance(strategy, dict) else None
    if not isinstance(items, list):
        return []
    plan = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not item.get('query'):
            continue
        plan_item = QueryPlanItem(
            query=str(item['query']),
            intent=str(item.get('intent') or 'gap_follow_up'),
            rationale=str(item.get('rationale') or 'Resumed from saved auto follow-up plan.'),
            site=item.get('site'),
        )
        key = _plan_key(plan_item.query, plan_item.site, plan_item.intent)
        if key in seen:
            continue
        seen.add(key)
        plan.append(plan_item)
    return plan


async def _safe_checkpoint_update(run_id: str, payload: dict, *, status: str, warnings: list[dict]) -> None:
    try:
        result = await asyncio.to_thread(update_research_run, run_id, payload, status=status)
    except (OSError, ValueError) as exc:
        warnings.append({'stage': 'checkpoint_update', 'status': status, 'message': str(exc)})
        return
    if not result.get('ok'):
        warnings.append(
            {
                'stage': 'checkpoint_update',
                'status': status,
                'message': str(result.get('message') or 'Checkpoint update failed.'),
            }
        )


def _merge_child_research_payload(
    plan_item: QueryPlanItem,
    payload: dict,
    *,
    searches: list,
    selection_trace: list,
    source_by_url: dict,
    evidence_by_key: dict,
    failures: list,
    blocked_sources: list,
) -> None:
    child_telemetry = payload.get('source_selection_telemetry') if isinstance(payload.get('source_selection_telemetry'), dict) else {}
    searches.append(
        {
            'query': plan_item.query,
            'intent': plan_item.intent,
            'rationale': plan_item.rationale,
            'site': plan_item.site,
            'ok': payload.get('ok', False),
            'provider': payload.get('search', {}).get('provider'),
            'result_count': len(payload.get('search', {}).get('results', [])),
            'source_count': len(payload.get('sources', [])),
            'selection_trace_count': len(payload.get('selection_trace', [])),
            'source_selection_telemetry': child_telemetry,
            'message': payload.get('message'),
        }
    )
    local_to_url = {}
    source_info_by_id = {}
    for source in payload.get('sources', []):
        source_url = source.get('final_url') or source.get('url')
        if not source_url:
            continue
        local_to_url[source.get('source_id')] = source_url
        reliability = source.get('reliability') if isinstance(source.get('reliability'), dict) else {}
        source_info_by_id[source.get('source_id')] = {
            'final_url': source.get('final_url'),
            'source_type': reliability.get('source_type'),
            'reliability_weight': reliability.get('reliability_weight'),
        }
        source_by_url.setdefault(source_url, source)
    for trace_item in payload.get('selection_trace', []):
        info = source_info_by_id.get(trace_item.get('source_id'), {})
        selection_trace.append(
            {
                'query': plan_item.query,
                'intent': plan_item.intent,
                'site': plan_item.site,
                **{key: value for key, value in info.items() if value is not None},
                **trace_item,
            }
        )
    for item in payload.get('evidence', []):
        evidence_url = item.get('url') or local_to_url.get(item.get('source_id'))
        evidence_text = item.get('text') or item.get('quote') or item.get('citation')
        key = (evidence_url, evidence_text)
        evidence_by_key.setdefault(key, dict(item, _source_url=evidence_url))
    failures.extend(payload.get('failures', []))
    blocked_sources.extend(payload.get('blocked_sources', []))


def _aggregate_child_source_selection_telemetry(
    searches: list,
    selection_trace: list,
    sources: list,
) -> dict:
    aggregate: dict = {
        'final_selected_source_count': sum(
            1 for item in selection_trace if isinstance(item, dict) and item.get('decision') in {'selected', 'selected_recovery'}
        ),
        'final_unique_source_count': len(sources),
    }
    summed_keys = (
        'planned_read_count',
        'attempted_read_count',
        'selected_source_count',
        'planned_authority_source_count',
        'selected_authority_source_count',
        'planned_low_value_source_count',
        'planned_policy_skip_count',
        'trace_policy_skip_count',
        'duplicate_skip_count',
        'read_failure_count',
        'recovery_selected_count',
        'cache_hit_source_count',
        'repeated_domain_count',
    )
    decision_counts: dict[str, int] = {}
    selection_reason_counts: dict[str, int] = {}
    score_reason_counts: dict[str, int] = {}
    repeated_domains: dict[str, int] = {}
    per_query: list[dict] = []

    for search in searches:
        if not isinstance(search, dict):
            continue
        telemetry = search.get('source_selection_telemetry') if isinstance(search.get('source_selection_telemetry'), dict) else {}
        if not telemetry:
            continue
        per_query.append(
            {
                'query': search.get('query'),
                'intent': search.get('intent'),
                'site': search.get('site'),
                'planned_read_count': telemetry.get('planned_read_count', 0),
                'selected_source_count': telemetry.get('selected_source_count', 0),
                'planned_authority_source_count': telemetry.get('planned_authority_source_count', 0),
                'selected_authority_source_count': telemetry.get('selected_authority_source_count', 0),
                'planned_low_value_source_count': telemetry.get('planned_low_value_source_count', 0),
                'read_failure_count': telemetry.get('read_failure_count', 0),
                'policy_skip_count': telemetry.get('trace_policy_skip_count', 0),
            }
        )
        for key in summed_keys:
            aggregate[key] = int(aggregate.get(key) or 0) + int(telemetry.get(key) or 0)
        for key, counts in (
            ('decision_counts', decision_counts),
            ('read_selection_reason_counts', selection_reason_counts),
        ):
            values = telemetry.get(key) if isinstance(telemetry.get(key), dict) else {}
            for name, count in values.items():
                counts[str(name)] = counts.get(str(name), 0) + int(count or 0)
        domains = telemetry.get('repeated_domains') if isinstance(telemetry.get('repeated_domains'), dict) else {}
        for domain, count in domains.items():
            repeated_domains[str(domain)] = repeated_domains.get(str(domain), 0) + int(count or 0)
        for item in telemetry.get('top_source_score_reasons', []) or []:
            if not isinstance(item, dict):
                continue
            reason = str(item.get('reason') or '').strip()
            if not reason:
                continue
            score_reason_counts[reason] = score_reason_counts.get(reason, 0) + int(item.get('count') or 0)

    if not per_query:
        fallback = build_source_selection_telemetry([], selection_trace, sources)
        fallback['final_selected_source_count'] = aggregate['final_selected_source_count']
        fallback['final_unique_source_count'] = aggregate['final_unique_source_count']
        fallback['query_count_with_telemetry'] = 0
        fallback['per_query'] = []
        return fallback

    aggregate['query_count_with_telemetry'] = len(per_query)
    aggregate['per_query'] = per_query[:12]
    if decision_counts:
        aggregate['decision_counts'] = dict(sorted(decision_counts.items(), key=lambda pair: (-pair[1], pair[0])))
    if selection_reason_counts:
        aggregate['read_selection_reason_counts'] = dict(sorted(selection_reason_counts.items(), key=lambda pair: (-pair[1], pair[0])))
    if repeated_domains:
        aggregate['repeated_domains'] = dict(sorted(repeated_domains.items(), key=lambda pair: (-pair[1], pair[0]))[:10])
    if score_reason_counts:
        aggregate['top_source_score_reasons'] = [
            {'reason': reason, 'count': count}
            for reason, count in sorted(score_reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]
        ]
    return aggregate


def _remap_sources_and_evidence(source_by_url: dict, evidence_by_key: dict) -> tuple[list, list, dict]:
    sources = []
    source_id_by_url = {}
    for index, (source_url, source) in enumerate(source_by_url.items(), start=1):
        remapped = dict(source)
        remapped['source_id'] = index
        source_id_by_url[source_url] = index
        if source.get('url'):
            source_id_by_url[source['url']] = index
        if source.get('final_url'):
            source_id_by_url[source['final_url']] = index
        sources.append(remapped)

    evidence = []
    for original in evidence_by_key.values():
        item = dict(original)
        source_url = item.pop('_source_url', None) or item.get('url')
        source_id = source_id_by_url.get(source_url)
        if source_id is not None:
            item['source_id'] = source_id
            if item.get('char_range'):
                start, end = item['char_range']
                item['citation'] = f'source:{source_id}[{start}:{end}]'
        evidence.append(item)
    evidence.sort(key=lambda item: (item.get('rank', 999), item.get('source_id', 999)))
    return sources, evidence, source_id_by_url


def _remap_selection_trace(selection_trace: list, source_id_by_url: dict) -> list:
    remapped_selection_trace = []
    for item in selection_trace:
        trace_item = dict(item)
        trace_url = trace_item.get('recovered_url') or trace_item.get('final_url') or trace_item.get('url')
        source_id = source_id_by_url.get(trace_url)
        if source_id is not None and trace_item.get('source_id') is not None:
            trace_item['source_id'] = source_id
        remapped_selection_trace.append(trace_item)
    return remapped_selection_trace


def _build_gap_follow_up_plan(question: str, provisional_payload: dict, completed_plan_keys: set, *, limit: int) -> list[QueryPlanItem]:
    if limit <= 0:
        return []
    research_quality = provisional_payload.get('research_quality') if isinstance(provisional_payload.get('research_quality'), dict) else {}
    source_quality = provisional_payload.get('source_quality') if isinstance(provisional_payload.get('source_quality'), dict) else {}
    research_coverage = provisional_payload.get('research_coverage') if isinstance(provisional_payload.get('research_coverage'), dict) else {}
    citation_audit = provisional_payload.get('citation_audit') if isinstance(provisional_payload.get('citation_audit'), dict) else {}
    source_freshness = provisional_payload.get('source_freshness') if isinstance(provisional_payload.get('source_freshness'), dict) else {}
    final_answer_review = provisional_payload.get('final_answer_review') if isinstance(provisional_payload.get('final_answer_review'), dict) else {}
    if (
        research_quality.get('label') == 'strong'
        and provisional_payload.get('citation_validation', {}).get('ok')
        and int(source_quality.get('unique_domain_count') or 0) >= 3
    ):
        return []
    claims = provisional_payload.get('claims', []) or []
    sources = provisional_payload.get('sources', []) or []
    blocked_sources = provisional_payload.get('blocked_sources', []) or []
    source_types = {
        str(source.get('reliability', {}).get('source_type') or '')
        for source in sources
        if isinstance(source.get('reliability'), dict)
    }
    has_primary_source = bool(source_types & {'government', 'academic', 'documentation', 'repository'})
    has_conflicts = any(claim.get('conflicting_sources') for claim in claims if isinstance(claim, dict))
    has_recent_change_notes = bool(provisional_payload.get('recent_changes'))
    single_source_claim_count = sum(1 for claim in claims if isinstance(claim, dict) and len(claim.get('supporting_sources', []) or []) < 2)

    candidates: list[tuple[str, str, str]] = []
    remediation_plan = (
        provisional_payload.get('remediation_plan')
        if isinstance(provisional_payload.get('remediation_plan'), dict)
        else build_research_remediation_plan(provisional_payload)
    )
    for action in remediation_plan.get('actions', []) or []:
        if not isinstance(action, dict) or not action.get('query'):
            continue
        candidates.append(
            (
                str(action['query']),
                str(action.get('reason') or f"Repair evidence gap: {action.get('gap_code') or 'unknown'}."),
                str(action.get('intent') or 'gap_follow_up'),
            )
        )
    if has_conflicts:
        candidates.append((f'{question} conflicting evidence comparison', 'Investigate unresolved claim conflicts.', 'contradiction_resolution'))
    if not has_primary_source and sources:
        candidates.append((f'{question} official documentation primary source', 'Find primary sources because current evidence is mostly secondary.', 'gap_follow_up'))
    if int(source_quality.get('unique_domain_count') or 0) < 2:
        candidates.append((f'{question} independent sources', 'Increase source diversity after initial searches.', 'gap_follow_up'))
    if len(provisional_payload.get('sources', []) or []) < 3:
        candidates.append((f'{question} additional evidence', 'Add coverage because the current source set is thin.', 'gap_follow_up'))
    if single_source_claim_count >= 2:
        candidates.append((f'{question} corroborating evidence', 'Corroborate claims currently supported by only one source.', 'gap_follow_up'))
    if blocked_sources:
        candidates.append((f'{question} alternate source mirror official', 'Find alternate accessible sources after blocked pages.', 'gap_follow_up'))
    if not has_recent_change_notes:
        candidates.append((f'{question} latest update changelog announcement', 'Check whether recent changes affect the answer.', 'gap_follow_up'))
    if not provisional_payload.get('citation_validation', {}).get('ok', True):
        candidates.append((f'{question} primary source', 'Repair weak or invalid citation coverage.', 'gap_follow_up'))
    for intent in research_coverage.get('missing_intents', []) or []:
        normalized_intent = str(intent).replace('_', ' ')
        candidates.append((f'{question} {normalized_intent}', f'Follow unsatisfied research plan intent: {intent}.', 'gap_follow_up'))
    for gap in research_coverage.get('gaps', []) or []:
        candidates.append((f'{question} {str(gap)[:80]}', 'Follow a coverage-audit gap.', 'gap_follow_up'))
    if not citation_audit.get('ok', True):
        candidates.append((f'{question} cited supporting evidence', 'Repair citation-audit issues.', 'gap_follow_up'))
    for gap in citation_audit.get('issues', []) or []:
        candidates.append((f'{question} {str(gap)[:80]}', 'Follow a citation-audit issue.', 'gap_follow_up'))
    if source_freshness.get('current_sensitive') and not source_freshness.get('content_freshness_evidence'):
        candidates.append((f'{question} changelog release notes latest', 'Find freshness evidence for a current-sensitive question.', 'gap_follow_up'))
    for gap in source_freshness.get('gaps', []) or []:
        candidates.append((f'{question} {str(gap)[:80]}', 'Follow a freshness-audit gap.', 'gap_follow_up'))
    for gap in research_quality.get('gaps', []) or []:
        candidates.append((f'{question} {str(gap)[:80]}', 'Follow a research-quality gap.', 'gap_follow_up'))
    for issue in final_answer_review.get('issues', []) or []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get('code') or '')
        severity = str(issue.get('severity') or '')
        if severity not in {'critical', 'high', 'medium'}:
            continue
        if code == 'citation_audit_failed':
            candidates.append((f'{question} cited evidence supporting claims', 'Reviewer agent found citation audit issues.', 'gap_follow_up'))
        elif code == 'coverage_gaps':
            candidates.append((f'{question} missing plan intent primary official evidence', 'Reviewer agent found coverage gaps.', 'gap_follow_up'))
        elif code == 'freshness_gaps':
            candidates.append((f'{question} latest official update release notes', 'Reviewer agent found freshness gaps.', 'gap_follow_up'))
        elif code == 'no_primary_sources':
            candidates.append((f'{question} official primary source documentation', 'Reviewer agent found no primary sources.', 'gap_follow_up'))
        elif code == 'low_domain_diversity':
            candidates.append((f'{question} independent corroborating sources', 'Reviewer agent found low domain diversity.', 'gap_follow_up'))
        elif code == 'single_source_claims':
            candidates.append((f'{question} corroborating evidence multiple sources', 'Reviewer agent found single-source claims.', 'gap_follow_up'))
        elif code == 'conflicted_claims':
            candidates.append((f'{question} conflicting evidence comparison', 'Reviewer agent found conflicted claims.', 'contradiction_resolution'))
        elif code == 'blocked_sources':
            candidates.append((f'{question} alternate accessible official source', 'Reviewer agent found blocked sources.', 'gap_follow_up'))
        elif code in {'no_readable_sources', 'low_research_quality'}:
            candidates.append((f'{question} authoritative source evidence', 'Reviewer agent found weak final-answer readiness.', 'gap_follow_up'))
    contradiction = (
        final_answer_review.get('contradiction_review')
        if isinstance(final_answer_review.get('contradiction_review'), dict)
        else {}
    )
    for item in contradiction.get('retrieval_plan', []) or []:
        if isinstance(item, dict) and item.get('query'):
            candidates.append((str(item['query']), str(item.get('rationale') or 'Resolve a disputed claim.'), str(item.get('intent') or 'contradiction_resolution')))
    for search in contradiction.get('follow_up_searches', []) or []:
        candidates.append((str(search), 'Reviewer agent generated contradiction-focused follow-up.', 'contradiction_resolution'))
    for search in provisional_payload.get('recommended_next_searches', []) or []:
        candidates.append((str(search), 'Follow a recommended next search from the gap analysis.', 'gap_follow_up'))

    seen: set[str] = set()
    plan: list[QueryPlanItem] = []
    for query, rationale, intent in candidates:
        normalized_query = ' '.join(query.split())
        if not normalized_query:
            continue
        normalized_intent = str(intent or 'gap_follow_up')
        key = _plan_key(normalized_query, None, normalized_intent)
        if key in completed_plan_keys or normalized_query.lower() in seen:
            continue
        seen.add(normalized_query.lower())
        plan.append(QueryPlanItem(normalized_query, normalized_intent, rationale))
        if len(plan) >= limit:
            break
    return plan


def _agent_loop_state(
    *,
    planned_queries: list[QueryPlanItem],
    completed_plan_keys: set,
    observed_gaps: list[str] | None = None,
    stop_reason: str | None = None,
    decisions: list[dict] | None = None,
    rounds: list[dict] | None = None,
) -> dict:
    completed_query_keys = {_plan_key(item.query, item.site, item.intent) for item in planned_queries if _plan_key(item.query, item.site, item.intent) in completed_plan_keys}
    return {
        'agents': [
            {'name': 'planner', 'role': 'Build initial and follow-up query plans.'},
            {'name': 'executor', 'role': 'Run searches and read sources.'},
            {'name': 'reviewer', 'role': 'Critique provisional answers and request targeted follow-up.'},
        ],
        'planned_queries': [item.to_dict() for item in planned_queries],
        'completed_queries': [
            item.to_dict()
            for item in planned_queries
            if _plan_key(item.query, item.site, item.intent) in completed_query_keys
        ],
        'remaining_queries': [
            item.to_dict()
            for item in planned_queries
            if _plan_key(item.query, item.site, item.intent) not in completed_query_keys
        ],
        'observed_gaps': list(observed_gaps or []),
        'stop_reason': stop_reason,
        'decisions': list(decisions or []),
        'rounds': list(rounds or []),
    }


def _search_backend_summary(searches: list[dict]) -> dict:
    provider_counts: dict[str, int] = {}
    failures = 0
    for search in searches:
        provider = str(search.get('provider') or 'unknown')
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        if not search.get('ok'):
            failures += 1
    return {
        'provider_counts': provider_counts,
        'search_count': len(searches),
        'failed_search_count': failures,
    }


def _parse_safe_continue_input(request: str) -> tuple[str, str]:
    lines = [line.strip() for line in str(request or '').splitlines()]
    lines = [line for line in lines if line]
    if len(lines) < 2:
        raise ValueError('Use first line as run_id and remaining lines as follow-up query.')
    run_id = lines[0]
    follow_up_query = ' '.join(lines[1:]).strip()
    if not follow_up_query:
        raise ValueError('Follow-up query is required.')
    return run_id, follow_up_query


def _parse_bool_text(value: str | bool | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {'1', 'true', 'yes', 'y', 'on', 'redact', 'private', 'private-share'}:
        return True
    if normalized in {'0', 'false', 'no', 'n', 'off', 'unredacted', 'public'}:
        return False
    return default


def _split_packaging_values(value: str) -> list[str]:
    cleaned = str(value or '').strip()
    while cleaned.startswith(('-', '*', '•')):
        cleaned = cleaned[1:].strip()
    if not cleaned:
        return []
    return [item.strip() for item in cleaned.split(',') if item.strip()]


def _parse_packaging_option_line(line: str) -> tuple[str, str] | None:
    normalized_keys = {
        'dry-run': 'dry_run',
        'dry_run': 'dry_run',
        'apply': 'apply',
        'export': 'export',
        'find': 'find',
        'freshness': 'freshness',
        'include-legacy-missing-pid': 'include_legacy_missing_pid',
        'include_legacy_missing_pid': 'include_legacy_missing_pid',
        'latest': 'latest',
        'limit': 'limit',
        'loop-id': 'loop_id',
        'loop_id': 'loop_id',
        'job-id': 'job_id',
        'job_id': 'job_id',
        'package-on-fail': 'package_on_fail',
        'package_on_fail': 'package_on_fail',
        'preview': 'preview',
        'priority': 'priority',
        'profile': 'profile',
        'query': 'query',
        'question': 'question',
        'redact': 'redact',
        'selector': 'selector',
        'status': 'status',
        'submit': 'submit',
        'tag': 'tag',
        'tags': 'tags',
        'run-id': 'run_id',
        'run-ids': 'run_ids',
        'run_id': 'run_id',
        'run_ids': 'run_ids',
        'source-pack': 'source_pack',
        'source_pack': 'source_pack',
        'zip': 'zip',
    }
    for separator in ('=', ':'):
        if separator not in line:
            continue
        key, value = line.split(separator, 1)
        normalized = key.strip().lower().replace(' ', '_')
        if normalized in normalized_keys:
            return normalized_keys[normalized], value.strip()
    return None


def _parse_safe_packaging_request(request: str) -> dict:
    lines = [line.strip() for line in str(request or '').splitlines() if line.strip()]
    options: dict[str, str | bool] = {}
    values: list[str] = []
    for line in lines:
        lower = line.lower()
        option = _parse_packaging_option_line(line)
        if option is not None:
            key, value = option
            options[key] = value
        elif lower in {'redact', 'private', 'private-share'}:
            options['redact'] = True
            if lower == 'private-share':
                options['profile'] = 'private-share'
        elif lower in {'unredacted', 'public'}:
            options['redact'] = False
        elif lower.startswith('latest '):
            options['latest'] = lower.split(None, 1)[1].strip()
        elif lower.startswith('find '):
            options['find'] = line.split(None, 1)[1].strip()
        elif lower.startswith('run_ids '):
            values.extend(_split_packaging_values(line.split(None, 1)[1]))
        else:
            values.extend(_split_packaging_values(line))
    if 'run_id' in options:
        values = _split_packaging_values(str(options['run_id'])) + values
    if 'run_ids' in options:
        values = _split_packaging_values(str(options['run_ids'])) + values
    if 'query' in options and 'find' not in options:
        options['find'] = str(options['query'])
    return {'options': options, 'values': values}


def _profile_redaction(options: dict, *, default: bool = False) -> bool:
    profile_name = options.get('profile')
    redact = _parse_bool_text(options.get('redact'), default=default)
    if profile_name:
        profile = get_work_profile(str(profile_name))
        redact = redact or profile.redact_exports
    return redact


def _packaging_dry_run(options: dict) -> bool:
    return _parse_bool_text(options.get('dry_run'), default=False) or _parse_bool_text(options.get('preview'), default=False)


def _dedupe_run_ids(run_ids: list[object]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in run_ids:
        run_id = str(value or '').strip()
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        deduped.append(run_id)
    return deduped


def _selected_run_ids_from_safe_request(parsed: dict, *, default_latest: int | None = None) -> dict:
    options = parsed.get('options') if isinstance(parsed.get('options'), dict) else {}
    values = list(parsed.get('values') or [])
    if values:
        return {'ok': True, 'selector': 'explicit', 'run_ids': _dedupe_run_ids(values)}
    if options.get('latest') is not None:
        try:
            latest = int(str(options.get('latest')).strip())
        except ValueError:
            return {'ok': False, 'message': 'latest must be an integer.'}
        return select_research_run_ids(latest=latest)
    if options.get('find'):
        return select_research_run_ids(find=str(options.get('find')), limit=5)
    if default_latest is not None:
        return select_research_run_ids(latest=default_latest)
    return {
        'ok': False,
        'message': 'Provide a run_id, latest=N, or find=query.',
        'expected_format': 'run_id on first line; optional lines: redact=true, profile=private-share, zip=true',
    }


def _parse_safe_mission_request(request: str) -> dict:
    lines = [line.strip() for line in str(request or '').splitlines() if line.strip()]
    options: dict[str, str | bool] = {}
    question_lines: list[str] = []
    for line in lines:
        option = _parse_packaging_option_line(line)
        if option is not None:
            key, value = option
            options[key] = value
            continue
        lower = line.lower()
        if lower in {'export', 'export=true'}:
            options['export'] = 'true'
        elif lower in {'source_pack', 'source-pack', 'source_pack=true', 'source-pack=true'}:
            options['source_pack'] = 'true'
        elif lower in {'dry_run', 'dry-run', 'preview'}:
            options['dry_run'] = 'true'
        else:
            question_lines.append(line)
    for question_key in ('query', 'question'):
        if question_key in options:
            question_lines.insert(0, str(options[question_key]))
    return {'question': ' '.join(question_lines).strip(), 'options': options}


def _mission_dry_run(options: dict) -> bool:
    return _parse_bool_text(options.get('dry_run'), default=False) or _parse_bool_text(options.get('preview'), default=False)


def _mission_payload_summary(payload: dict, profile_name: str, *, min_score: int | None) -> dict:
    sources = list(payload.get('sources', []) or [])
    evidence = list(payload.get('evidence', []) or [])
    claims = list(payload.get('claims', []) or [])
    failures = list(payload.get('failures', []) or [])
    blocked_sources = list(payload.get('blocked_sources', []) or [])
    quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    answer_readiness = payload.get('answer_readiness') if isinstance(payload.get('answer_readiness'), dict) else {}
    score = int(quality.get('score') or 0)
    score_gate_ok = min_score is None or score >= min_score
    answer_gate_ok = True if not answer_readiness else bool(answer_readiness.get('ok'))
    quality_gate_ok = score_gate_ok and answer_gate_ok
    return {
        'ok': bool(payload.get('ok')) and quality_gate_ok,
        'profile': profile_name,
        'run_id': payload.get('run_id'),
        'run_path': payload.get('run_path'),
        'final_report_path': payload.get('final_report_path'),
        'report_format': payload.get('report_format'),
        'quality_gate': {
            'ok': quality_gate_ok,
            'min_score': min_score,
            'score': score,
            'label': quality.get('label'),
            'score_gate_ok': score_gate_ok,
            'answer_gate_ok': answer_gate_ok,
            'answer_readiness_label': answer_readiness.get('label') if answer_readiness else None,
            'answer_readiness_score': answer_readiness.get('score') if answer_readiness else None,
            'answer_readiness_blockers': list(answer_readiness.get('blockers', []) or [])[:5] if answer_readiness else [],
            'answer_readiness_warnings': list(answer_readiness.get('warnings', []) or [])[:5] if answer_readiness else [],
        },
        'counts': {
            'sources': len(sources),
            'evidence': len(evidence),
            'claims': len(claims),
            'failures': len(failures),
            'blocked_sources': len(blocked_sources),
        },
        'research_quality': payload.get('research_quality'),
        'source_quality': payload.get('source_quality'),
        'research_coverage': payload.get('research_coverage'),
        'source_freshness': payload.get('source_freshness'),
        'answer_readiness': payload.get('answer_readiness'),
        'agent_loop': payload.get('agent_loop'),
        'recommended_next_searches': list(payload.get('recommended_next_searches', []) or [])[:5],
    }


def _parse_safe_work_loop_status_request(request: str) -> dict:
    lines = [line.strip() for line in str(request or '').splitlines() if line.strip()]
    options: dict[str, str] = {}
    values: list[str] = []
    for line in lines:
        local_option = None
        for separator in ('=', ':'):
            if separator in line:
                key, value = line.split(separator, 1)
                normalized = key.strip().lower().replace(' ', '_').replace('-', '_')
                if normalized in {'review_failed', 'review_note', 'note'}:
                    local_option = (normalized, value.strip())
                break
        if local_option is not None:
            key, value = local_option
            options[key] = value
            continue
        option = _parse_packaging_option_line(line)
        if option is not None:
            key, value = option
            options[key] = value
            continue
        lower = line.lower()
        if lower in {'active', 'running', 'in_progress', 'in-progress'}:
            options['selector'] = 'active'
        elif lower in {'latest', 'recent', 'status', 'summary', 'loops', 'stale'}:
            options['selector'] = lower
        elif lower in {'review_failed', 'review-failed', 'acknowledge_failed', 'acknowledge-failed'}:
            options['review_failed'] = 'true'
        elif lower.startswith('limit '):
            options['limit'] = lower.split(None, 1)[1].strip()
        elif lower.startswith('latest '):
            options['selector'] = 'latest'
            options['limit'] = lower.split(None, 1)[1].strip()
        else:
            values.extend(_split_packaging_values(line))
    if 'latest' in options and 'limit' not in options:
        options['selector'] = 'latest'
        options['limit'] = str(options['latest'])
    if 'loop_id' in options:
        values = _split_packaging_values(str(options['loop_id'])) + values
    if 'find' in options and 'selector' not in options:
        options['selector'] = str(options['find'])
    if 'query' in options and 'selector' not in options:
        options['selector'] = str(options['query'])
    return {'options': options, 'values': values}


def _parse_safe_research_job_request(request: str) -> dict:
    lines = [line.strip() for line in str(request or '').splitlines() if line.strip()]
    options: dict[str, str] = {}
    values: list[str] = []
    request_lines: list[str] = []
    for line in lines:
        option = _parse_packaging_option_line(line)
        if option is not None:
            key, value = option
            options[key] = value
            continue
        lower = line.lower()
        if lower in {'queued', 'leased', 'running', 'completed', 'failed', 'cancelled', 'canceled', 'stale'}:
            options['status'] = 'cancelled' if lower == 'canceled' else lower
        elif lower in {'latest', 'recent', 'jobs', 'status', 'summary'}:
            options['selector'] = lower
        elif lower in {'apply', 'submit', 'queue'}:
            options['submit'] = 'true'
        elif lower in {'dry_run', 'dry-run', 'preview'}:
            options['dry_run'] = 'true'
        elif lower.startswith('limit '):
            options['limit'] = lower.split(None, 1)[1].strip()
        elif lower.startswith('job_id '):
            values.extend(_split_packaging_values(line.split(None, 1)[1]))
        else:
            request_lines.append(line)
    if 'job_id' in options:
        values = _split_packaging_values(str(options['job_id'])) + values
    for question_key in ('query', 'question'):
        if question_key in options:
            request_lines.insert(0, str(options[question_key]))
    tags: list[str] = []
    for key in ('tag', 'tags'):
        if key in options:
            tags.extend(_split_packaging_values(str(options[key])))
    return {
        'options': options,
        'values': values,
        'request': ' '.join(request_lines).strip(),
        'tags': tags,
    }


def _parse_safe_checkpoint_request(request: str) -> dict:
    lines = [line.strip() for line in str(request or '').splitlines() if line.strip()]
    options: dict[str, str] = {}
    values: list[str] = []
    for line in lines:
        option = _parse_packaging_option_line(line)
        if option is not None:
            key, value = option
            options[key] = value
            continue
        lower = line.lower()
        if lower in {'in_progress', 'in-progress', 'running', 'interrupted'}:
            options['status'] = 'in_progress' if lower in {'in-progress', 'running'} else lower
        elif lower in {'latest', 'recent', 'checkpoints', 'status', 'summary'}:
            options['selector'] = lower
        elif lower in {'apply', 'interrupt'}:
            options['apply'] = 'true'
        elif lower in {'dry_run', 'dry-run', 'preview'}:
            options['dry_run'] = 'true'
        elif lower.startswith('limit '):
            options['limit'] = lower.split(None, 1)[1].strip()
        elif lower.startswith('run_id '):
            values.extend(_split_packaging_values(line.split(None, 1)[1]))
        else:
            values.extend(_split_packaging_values(line))
    if 'run_id' in options:
        values = _split_packaging_values(str(options['run_id'])) + values
    if 'run_ids' in options:
        values = _split_packaging_values(str(options['run_ids'])) + values
    return {'options': options, 'values': _dedupe_run_ids(values)}


def _research_job_apply_requested(options: dict) -> bool:
    return _parse_bool_text(options.get('apply'), default=False) or _parse_bool_text(options.get('submit'), default=False)


def _tail_text_file(path: Path, *, limit: int = 5, max_chars: int = 4000) -> list[str]:
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    return [line[:max_chars] for line in lines[-max(0, limit) :]]


def _run_action_hints(metadata: dict) -> list[dict]:
    run_id = metadata.get('run_id')
    kind = metadata.get('kind')
    status = metadata.get('status')
    actions = []
    if run_id and kind == 'deep_research' and status == 'in_progress':
        actions.append(
            {
                'tool': 'safe_resume_deep_research',
                'reason': 'Run is an interrupted deep_research checkpoint.',
                'example': f'safe_resume_deep_research(run_id="{run_id}")',
            }
        )
    if run_id and status == 'completed':
        actions.append(
            {
                'tool': 'safe_continue_research_run',
                'reason': 'Run is completed and can be extended with a follow-up query.',
                'example': f'safe_continue_research_run(request="{run_id}\\n<follow-up query>")',
            }
        )
        actions.append(
            {
                'tool': 'safe_export_research_run',
                'reason': 'Run is completed and can be exported as a review/share bundle.',
                'example': f'safe_export_research_run(request="{run_id}\\nprofile=private-share")',
            }
        )
        actions.append(
            {
                'tool': 'safe_build_source_pack',
                'reason': 'Run is completed and can be included in a redacted source handoff pack.',
                'example': f'safe_build_source_pack(request="{run_id}")',
            }
        )
    return actions


@mcp.custom_route('/health', methods=['GET'], include_in_schema=False)
async def health_check(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            'ok': True,
            'service': 'lmstudio-web-research',
            'transport': settings.mcp_transport,
            'host': settings.mcp_host,
            'port': settings.mcp_port,
            'streamable_http_path': settings.mcp_streamable_http_path,
            'sse_path': settings.mcp_sse_path,
            'cache': cache.stats(),
        }
    )


@mcp.tool()
def safe_web_search(query: str) -> dict:
    '''Low-risk one-parameter web search. Prefer this when the model struggles with tool-call XML.'''
    return run_web_search(query=query, max_results=8, freshness=None, site=None)


def _extract_tool_call_parameter(raw: str, name: str) -> str:
    marker = f'<parameter={name}>'
    start = raw.find(marker)
    if start < 0:
        return ''
    start += len(marker)
    end = raw.find('</parameter>', start)
    return raw[start:end if end >= 0 else None].strip()


def _extract_unnamed_parameter_text(raw: str) -> str:
    start = raw.find('<parameter>')
    if start < 0:
        return ''
    start += len('<parameter>')
    end = raw.find('</parameter>', start)
    return raw[start:end if end >= 0 else None].strip()


def repair_lmstudio_tool_call(raw: str) -> dict:
    '''Convert common malformed LM Studio XML tool calls into safer one-parameter tool calls.'''
    text = str(raw or '').strip()
    query = _extract_tool_call_parameter(text, 'query') or _extract_tool_call_parameter(text, 'question')
    if not query:
        query = _extract_unnamed_parameter_text(text)
    function = ''
    function_start = text.find('<function=')
    if function_start >= 0:
        function_start += len('<function=')
        function_end = text.find('>', function_start)
        function = text[function_start:function_end if function_end >= 0 else None].strip()
    safe_tool = {
        'web_search': 'safe_web_search',
        'research_web': 'safe_research',
        'deep_research': 'safe_deep_research',
        'read_url': 'safe_read_url',
        'discover_links': 'safe_read_url',
    }.get(function, function if function.startswith('safe_') else 'safe_web_search')
    parameter_name = {
        'safe_read_url': 'url',
        'safe_deep_research': 'question',
    }.get(safe_tool, 'query' if safe_tool in {'safe_web_search', 'safe_research'} else 'request')
    repaired = ''
    if query:
        repaired = (
            '<tool_call>\n'
            f'<function={safe_tool}>\n'
            f'<parameter={parameter_name}>\n'
            f'{query}\n'
            '</parameter>\n'
            '</function>\n'
            '</tool_call>'
        )
    return {
        'ok': bool(query),
        'tool': 'safe_repair_tool_call',
        'detected_function': function or None,
        'recommended_tool': safe_tool,
        'recommended_parameter': parameter_name,
        'repaired_tool_call': repaired,
        'message': 'Use the repaired one-parameter safe tool call.' if query else 'Could not find a query/url parameter to repair.',
    }


@mcp.tool()
def safe_repair_tool_call(raw: str) -> dict:
    '''One-parameter helper that repairs common malformed LM Studio XML tool calls into safe tool calls.'''
    return repair_lmstudio_tool_call(raw)


def _agent_request_lines(request: str) -> list[str]:
    return [line.strip() for line in str(request or '').splitlines() if line.strip()]


def _agent_request_text(request: str) -> str:
    return ' '.join(_agent_request_lines(request)).strip()


def _agent_option_value(lines: list[str], key: str) -> str:
    prefix = key.lower() + ':'
    equals_prefix = key.lower() + '='
    for line in lines:
        lowered = line.lower()
        if lowered.startswith(prefix):
            return line.split(':', 1)[1].strip()
        if lowered.startswith(equals_prefix):
            return line.split('=', 1)[1].strip()
    return ''


def _agent_prefixed_value(lines: list[str], *prefixes: str) -> str:
    normalized = tuple(prefix.lower().rstrip(':') + ':' for prefix in prefixes)
    for line in lines:
        lowered = line.lower()
        for prefix in normalized:
            if lowered.startswith(prefix):
                return line.split(':', 1)[1].strip()
    return ''


URL_PATTERN = re.compile(r'https?://[^\s<>"\')\]]+')


def _agent_urls(text: str) -> list[str]:
    urls = []
    seen = set()
    for match in URL_PATTERN.finditer(str(text or '')):
        url = match.group(0).rstrip('.,;:')
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _agent_requested_action(lines: list[str], text: str) -> str:
    explicit = _agent_option_value(lines, 'tool') or _agent_option_value(lines, 'mode') or _agent_option_value(lines, 'action')
    lowered = (explicit or text).lower()
    if explicit and lowered in {'deep', 'safe_deep_research', 'inline_deep', 'inline deep', 'deep_research'}:
        return 'deep'
    if 'director_id' in lowered or 'safe_research_director' in lowered or lowered.startswith('director'):
        return 'director'
    if 'worker' in lowered or 'queue' in lowered or 'job status' in lowered or 'safe_research_runtime' in lowered:
        return 'runtime'
    if 'campaign_id' in lowered or 'safe_research_campaign' in lowered or lowered.startswith('campaign'):
        return 'campaign'
    if 'synthesize' in lowered or 'safe_synthesize_research_campaign' in lowered:
        return 'synthesize'
    urls = _agent_urls(text)
    if lowered.startswith('http://') or lowered.startswith('https://') or 'safe_read_url' in lowered:
        return 'read'
    if urls and any(marker in lowered for marker in ('read ', 'fetch ', 'open ', 'extract ', 'summarize ', 'summarise ', 'source:')):
        return 'read'
    if any(marker in lowered for marker in ('deep research', 'giga', 'huge', 'long research', 'big research', 'exhaustive')):
        return 'runtime'
    if any(marker in lowered for marker in ('report', 'due diligence', 'market scan')):
        return 'runtime'
    if 'search' in lowered or 'find urls' in lowered or 'source discovery' in lowered or 'safe_web_search' in lowered:
        return 'search'
    return 'research'


@mcp.tool()
async def safe_research_agent(request: str) -> dict:
    '''Single-entry research tool. Send one request string; it routes to search, read, research, runtime, campaign, or director.'''
    lines = _agent_request_lines(request)
    text = _agent_request_text(request)
    if not text:
        return {
            'ok': False,
            'tool': 'safe_research_agent',
            'message': 'Send a URL, search query, research question, or runtime/director command in the request parameter.',
        }
    action = _agent_requested_action(lines, text)
    if action == 'read':
        detected_urls = _agent_urls(text)
        url = _agent_option_value(lines, 'url') or (detected_urls[0] if detected_urls else lines[0])
        payload = await safe_read_url(url)
        if isinstance(payload, dict) and detected_urls:
            payload.setdefault('detected_urls', detected_urls)
            payload.setdefault('detected_url_count', len(detected_urls))
    elif action == 'search':
        query = _agent_option_value(lines, 'query') or _agent_option_value(lines, 'question') or _agent_prefixed_value(lines, 'search') or text
        payload = safe_web_search(query)
    elif action == 'deep':
        question = (
            _agent_option_value(lines, 'question')
            or _agent_option_value(lines, 'query')
            or _agent_prefixed_value(lines, 'deep research', 'report', 'research')
            or text
        )
        payload = await safe_deep_research(question)
    elif action == 'runtime':
        runtime_request = request
        if not (
            _parse_bool_text(_agent_option_value(lines, 'submit'), default=False)
            or _parse_bool_text(_agent_option_value(lines, 'queue'), default=False)
            or _parse_bool_text(_agent_option_value(lines, 'apply'), default=False)
        ):
            runtime_request = f'{text}\nsubmit=true\nstart_worker=true\napply=true'
        payload = safe_research_runtime(runtime_request)
    elif action == 'campaign':
        payload = safe_research_campaign(request)
    elif action == 'synthesize':
        payload = safe_synthesize_research_campaign(request)
    elif action == 'director':
        payload = safe_research_director(request)
    else:
        query = _agent_option_value(lines, 'query') or _agent_option_value(lines, 'question') or _agent_prefixed_value(lines, 'research') or text
        payload = await safe_research(query)
    if isinstance(payload, dict):
        payload.setdefault('routed_by', 'safe_research_agent')
        payload.setdefault('routed_action', action)
    return payload


@mcp.tool()
async def safe_read_url(url: str) -> dict:
    '''Low-risk one-parameter URL reader. Reads a page or PDF with conservative defaults.'''
    payload = await run_read_url(url=url, query=None, render=False, source_id=1)
    return compact_read_payload(payload)


@mcp.tool()
async def safe_research(query: str) -> dict:
    '''Low-risk one-parameter research pass with conservative local-model defaults.'''
    payload = await run_research_web(
        query=query,
        max_results=8,
        read_top=2,
        freshness=None,
        site=None,
        render=False,
        report_format='executive_brief',
    )
    return compact_research_payload(payload)


@mcp.tool()
async def safe_deep_research(question: str) -> dict:
    '''Low-risk one-parameter deep research with compact results and conservative defaults.'''
    payload = await _run_deep_research(
        question,
        breadth=3,
        read_top_per_query=1,
        freshness=None,
        render=False,
        report_format='executive_brief',
        follow_up_rounds=1,
    )
    return compact_research_payload(payload)


@mcp.tool()
async def safe_research_mission(request: str) -> dict:
    '''One-parameter orchestrated research mission with profile defaults, quality gate, and optional packaging.'''
    parsed = _parse_safe_mission_request(request)
    options = parsed['options']
    question = parsed['question']
    if not question:
        return {
            'ok': False,
            'message': 'Research mission needs a question or topic.',
            'expected_format': 'question text plus optional lines like profile=careful, export=true, source_pack=true',
        }
    try:
        profile = get_work_profile(str(options.get('profile') or 'careful'))
    except ValueError as exc:
        return {'ok': False, 'message': str(exc)}
    export_requested = _parse_bool_text(options.get('export'), default=False)
    source_pack_requested = _parse_bool_text(options.get('source_pack'), default=False) or _parse_bool_text(
        options.get('source-pack'), default=False
    )
    package_on_fail = _parse_bool_text(options.get('package_on_fail'), default=False)
    redact = _profile_redaction(options, default=profile.redact_exports)
    if _mission_dry_run(options):
        return {
            'ok': True,
            'dry_run': True,
            'tool': 'safe_research_mission',
            'question': question,
            'profile': profile.to_dict(),
            'planned_research': {
                'breadth': profile.research_breadth,
                'read_top_per_query': profile.read_top_per_query,
                'follow_up_rounds': profile.follow_up_rounds,
                'report_format': profile.report_format,
                'render': profile.render,
                'min_score': profile.min_score,
            },
            'planned_packaging': {
                'export': export_requested,
                'source_pack': source_pack_requested,
                'redacted': redact,
                'package_on_fail': package_on_fail,
            },
            'message': 'Preview only. No research, export, or source-pack files were written.',
        }

    payload = await _run_deep_research(
        question,
        breadth=profile.research_breadth,
        read_top_per_query=profile.read_top_per_query,
        freshness=options.get('freshness') if options.get('freshness') else None,
        render=profile.render,
        report_format=profile.report_format,
        follow_up_rounds=profile.follow_up_rounds,
    )
    mission = {
        'ok': bool(payload.get('ok')),
        'tool': 'safe_research_mission',
        'question': question,
        **_mission_payload_summary(payload, profile.name, min_score=profile.min_score),
        'packaging': {},
        'payload': compact_research_payload(payload),
    }
    run_id = str(payload.get('run_id') or '')
    should_package = bool(mission['ok']) or package_on_fail
    if run_id and not should_package and (export_requested or source_pack_requested):
        mission['packaging']['skipped'] = True
        mission['packaging']['reason'] = 'quality_gate_failed'
        mission['packaging']['message'] = 'Packaging was skipped because the mission did not pass its quality gate.'
    if run_id and should_package and export_requested:
        export_dir = MCP_EXPORT_ROOT / uuid.uuid4().hex[:10]
        mission['packaging']['export'] = export_research_run(run_id, output_dir=export_dir, redact=redact)
        mission['packaging']['export']['run_ids'] = [run_id]
        mission['packaging']['export']['run_count'] = 1
    if run_id and should_package and source_pack_requested:
        pack_dir = MCP_SOURCE_PACK_ROOT / uuid.uuid4().hex[:10]
        pack = collect_source_pack([run_id], redact=redact)
        mission['packaging']['source_pack'] = write_source_pack(pack, pack_dir)
        mission['packaging']['source_pack']['run_ids'] = [run_id]
        mission['packaging']['source_pack']['run_count'] = 1
    if not run_id and (export_requested or source_pack_requested):
        mission['packaging']['message'] = 'Research run was not persisted, so packaging was skipped.'
    return mission


@mcp.tool()
def safe_research_runtime(request: str) -> dict:
    '''One-parameter background research runtime: preview/submit missions, start worker, and poll status.'''
    try:
        return research_mission_runtime(
            request,
            jobs_root=MCP_RESEARCH_JOBS_ROOT,
            runs_root=MCP_RESEARCH_RUNS_ROOT,
            worker_state_dir=MCP_RESEARCH_WORKER_STATE_DIR,
        )
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'tool': 'safe_research_runtime', 'message': f'Could not run research runtime command: {exc}'}


@mcp.tool()
def safe_research_campaign(request: str) -> dict:
    '''One-parameter multi-job research campaign planner. Previews by default; queues campaign jobs only with apply=true.'''
    try:
        parsed = parse_campaign_request(request)
        options = parsed['options']
        values = [str(value).strip() for value in parsed['values'] if str(value).strip()]
        dry_run = not _parse_bool_text(options.get('apply'), default=False)
        limit = int(str(options.get('limit') or '10').strip())
        if values:
            loaded = load_research_campaign(MCP_RESEARCH_CAMPAIGN_ROOT, values[0])
            if not loaded.get('ok'):
                return {'ok': False, 'tool': 'safe_research_campaign', **loaded}
            return {
                'ok': True,
                'tool': 'safe_research_campaign',
                'selector': 'explicit',
                'campaign': summarize_campaign(
                    loaded['campaign'],
                    jobs_root=MCP_RESEARCH_JOBS_ROOT,
                    runs_root=MCP_RESEARCH_RUNS_ROOT,
                ),
            }
        if str(options.get('action') or '').lower() in {'status', 'list', 'latest'} and not parsed['objective']:
            result = list_research_campaigns(
                MCP_RESEARCH_CAMPAIGN_ROOT,
                limit=limit,
                jobs_root=MCP_RESEARCH_JOBS_ROOT,
                runs_root=MCP_RESEARCH_RUNS_ROOT,
            )
            result['tool'] = 'safe_research_campaign'
            result['selector'] = str(options.get('action') or 'latest')
            return result
        objective = parsed['objective']
        if not objective:
            return {
                'ok': False,
                'tool': 'safe_research_campaign',
                'message': 'Research campaign needs an objective, or use status/list.',
            }
        profile = str(options.get('profile') or 'careful')
        depth = normalize_campaign_depth(str(options.get('depth') or 'standard'))
        priority = int(str(options.get('priority') or '0').strip())
        queue = _parse_bool_text(options.get('queue'), default=False) or _parse_bool_text(options.get('submit'), default=False)
        if dry_run:
            get_work_profile(profile)
            steps = plan_campaign_questions(objective, depth=depth)
            return {
                'ok': True,
                'tool': 'safe_research_campaign',
                'dry_run': True,
                'would_queue_jobs': bool(queue),
                'planned_campaign': {
                    'objective': objective,
                    'profile': profile,
                    'depth': depth,
                    'priority': priority,
                    'step_count': len(steps),
                    'steps': steps,
                },
                'message': 'Preview only. Add apply=true and queue=true to create queued campaign jobs.',
            }
        result = create_research_campaign(
            MCP_RESEARCH_CAMPAIGN_ROOT,
            objective=objective,
            profile=profile,
            depth=depth,
            priority=priority,
            queue=queue,
            jobs_root=MCP_RESEARCH_JOBS_ROOT,
        )
        result['tool'] = 'safe_research_campaign'
        result['dry_run'] = False
        return result
    except ValueError as exc:
        return {'ok': False, 'tool': 'safe_research_campaign', 'message': str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'tool': 'safe_research_campaign', 'message': f'Could not run research campaign command: {exc}'}


@mcp.tool()
def safe_research_director(request: str) -> dict:
    '''One-parameter autonomous research director. Previews by default; creates campaigns/follow-ups/synthesis only with apply=true.'''
    try:
        return research_director_command(
            request,
            root=MCP_RESEARCH_DIRECTOR_ROOT,
            campaign_root=MCP_RESEARCH_CAMPAIGN_ROOT,
            jobs_root=MCP_RESEARCH_JOBS_ROOT,
            runs_root=MCP_RESEARCH_RUNS_ROOT,
            synthesis_root=MCP_DIRECTOR_SYNTHESIS_ROOT,
            worker_state_dir=MCP_RESEARCH_WORKER_STATE_DIR,
        )
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'tool': 'safe_research_director', 'message': f'Could not run research director command: {exc}'}


@mcp.tool()
def safe_synthesize_research_campaign(request: str) -> dict:
    '''One-parameter campaign synthesis/export tool. Previews by default; writes dossier bundles only with apply=true.'''
    try:
        parsed = parse_campaign_request(request)
        options = parsed['options']
        values = [str(value).strip() for value in parsed['values'] if str(value).strip()]
        campaign_id = str(options.get('campaign_id') or '').strip() or (values[0] if values else '')
        if not campaign_id:
            return {
                'ok': False,
                'tool': 'safe_synthesize_research_campaign',
                'message': 'Campaign synthesis needs a campaign_id.',
            }
        dry_run = not _parse_bool_text(options.get('apply'), default=False)
        redact = _parse_bool_text(options.get('redact'), default=False)
        local_synthesis = _parse_bool_text(options.get('local_synthesis'), default=False) or _parse_bool_text(
            options.get('llm_synthesis'), default=False
        )
        synthesis = build_campaign_synthesis(
            campaign_id,
            campaign_root=MCP_RESEARCH_CAMPAIGN_ROOT,
            jobs_root=MCP_RESEARCH_JOBS_ROOT,
            runs_root=MCP_RESEARCH_RUNS_ROOT,
            redact=redact,
        )
        if synthesis.get('ok') and local_synthesis:
            synthesis = asyncio.run(apply_campaign_narrative_synthesis(synthesis, enabled=True))
        if dry_run:
            if not synthesis.get('ok'):
                return {'tool': 'safe_synthesize_research_campaign', **synthesis}
            return {
                'ok': True,
                'tool': 'safe_synthesize_research_campaign',
                'dry_run': True,
                'campaign_id': campaign_id,
                'run_count': synthesis.get('run_count'),
                'source_count': synthesis.get('source_count'),
                'claim_count': synthesis.get('claim_count'),
                'missing_runs': synthesis.get('missing_runs'),
                'campaign_synthesis': synthesis.get('campaign_synthesis'),
                'redacted': redact,
                'message': 'Preview only. Add apply=true to write the campaign synthesis bundle.',
            }
        result = write_campaign_synthesis_bundle(
            campaign_id,
            campaign_root=MCP_RESEARCH_CAMPAIGN_ROOT,
            jobs_root=MCP_RESEARCH_JOBS_ROOT,
            runs_root=MCP_RESEARCH_RUNS_ROOT,
            output_dir=MCP_CAMPAIGN_SYNTHESIS_ROOT,
            redact=redact,
            synthesis=synthesis,
        )
        result['tool'] = 'safe_synthesize_research_campaign'
        result['dry_run'] = False
        return result
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'tool': 'safe_synthesize_research_campaign', 'message': f'Could not synthesize research campaign: {exc}'}


@mcp.tool()
async def safe_resume_deep_research(run_id: str) -> dict:
    '''Low-risk one-parameter resume wrapper for interrupted deep research runs.'''
    return await resume_deep_research(run_id)


@mcp.tool()
def web_search(query: str, max_results: int = 10, freshness: str | None = None, site: str | None = None) -> dict:
    '''Search the open web and return normalized result URLs/snippets for follow-up reading.'''
    return run_web_search(query=query, max_results=max_results, freshness=freshness, site=site)


@mcp.tool()
async def read_url(url: str, query: str | None = None, render: bool = False) -> dict:
    '''Read one web page or PDF URL and return extracted text plus query-focused evidence.'''
    payload = await run_read_url(url=url, query=query, render=render, source_id=1)
    return compact_read_payload(payload)


@mcp.tool()
async def discover_links(
    url: str,
    query: str | None = None,
    render: bool = False,
    file_types: list[str] | None = None,
    limit: int = 50,
) -> dict:
    '''List links and online files from a page so the model can choose follow-up sources.'''
    return await run_discover_links(url=url, query=query, render=render, file_types=file_types, limit=limit)


@mcp.tool()
async def research_web(
    query: str,
    max_results: int = 8,
    read_top: int = 4,
    freshness: str | None = None,
    site: str | None = None,
    render: bool = False,
    report_format: str = 'long_report',
    source_intent: str | None = None,
) -> dict:
    '''Search the web, read top results, rank evidence, and return citation-ready sources.'''
    payload = await run_research_web(
        query=query,
        max_results=max_results,
        read_top=read_top,
        freshness=freshness,
        site=site,
        render=render,
        report_format=report_format,
        source_intent=source_intent,
    )
    return compact_research_payload(payload)


@mcp.tool()
async def deep_research(
    question: str,
    breadth: int = 3,
    read_top_per_query: int = 1,
    freshness: str | None = None,
    render: bool = False,
    report_format: str = 'executive_brief',
    follow_up_rounds: int = 1,
) -> dict:
    '''Run several related searches, read top sources, dedupe evidence, and return a research dossier.'''
    payload = await _run_deep_research(
        question,
        breadth=breadth,
        read_top_per_query=read_top_per_query,
        freshness=freshness,
        render=render,
        report_format=report_format,
        follow_up_rounds=follow_up_rounds,
    )
    return compact_research_payload(payload)


async def _run_deep_research(
    question: str,
    *,
    breadth: int,
    read_top_per_query: int,
    freshness: str | None,
    render: bool,
    report_format: str = 'long_report',
    follow_up_rounds: int = 0,
    checkpoint_run_id: str | None = None,
    resume_payload: dict | None = None,
) -> dict:
    started_at = time.monotonic()
    soft_timeout_seconds = float(getattr(settings, 'deep_research_soft_timeout_seconds', 35) or 0)
    phase_diagnostics: list[dict] = []

    def elapsed_seconds() -> float:
        return round(time.monotonic() - started_at, 3)

    def soft_timeout_reached() -> bool:
        return soft_timeout_seconds > 0 and (time.monotonic() - started_at) >= soft_timeout_seconds

    breadth = max(1, min(breadth, 6))
    read_top_per_query = max(1, min(read_top_per_query, 3))
    report_format = normalize_report_format(report_format)
    follow_up_rounds = max(0, min(follow_up_rounds, 3))
    topic_profile = classify_topic(question)
    query_plan = _load_saved_query_plan(resume_payload) or build_query_plan(question, breadth=breadth)
    auto_follow_up_plan: list[QueryPlanItem] = _load_saved_auto_follow_up_plan(resume_payload)
    checkpoint_warnings: list[dict] = list((resume_payload or {}).get('checkpoint_warnings', []) or [])
    saved_agent_loop = (resume_payload or {}).get('agent_loop') if isinstance((resume_payload or {}).get('agent_loop'), dict) else {}
    agent_loop_decisions: list[dict] = list(saved_agent_loop.get('decisions', []) or [])
    agent_loop_rounds: list[dict] = list(saved_agent_loop.get('rounds', []) or [])

    searches = list((resume_payload or {}).get('searches', []) or [])
    failures = list((resume_payload or {}).get('failures', []) or [])
    blocked_sources = list((resume_payload or {}).get('blocked_sources', []) or [])
    selection_trace = list((resume_payload or {}).get('selection_trace', []) or [])
    source_by_url = {}
    for source in (resume_payload or {}).get('sources', []) or []:
        source_url = source.get('final_url') or source.get('url')
        if source_url:
            source_by_url.setdefault(source_url, source)
    evidence_by_key = {}
    for item in (resume_payload or {}).get('evidence', []) or []:
        evidence_url = item.get('url')
        evidence_text = item.get('text') or item.get('quote') or item.get('citation')
        evidence_by_key.setdefault((evidence_url, evidence_text), dict(item, _source_url=evidence_url))
    completed_plan_keys = {
        _plan_key(item.get('query'), item.get('site'), item.get('intent'))
        for item in searches
        if item.get('query') and item.get('intent') != 'checkpoint'
    }
    if not agent_loop_decisions:
        agent_loop_decisions.append(
            {
                'round': 'initial',
                'agent': 'planner',
                'decision': 'planned_queries',
                'planned_query_count': len(query_plan),
                'breadth': breadth,
                'read_top_per_query': read_top_per_query,
            }
        )
    no_new_follow_up_rounds = 0
    if checkpoint_run_id is None and resume_payload is None:
        phase_started = time.monotonic()
        checkpoint_seed = {
            'ok': False,
            'question': question,
            'report_format': report_format,
            'strategy': {
                'mode': 'free_local_multi_search',
                'breadth': breadth,
                'read_top_per_query': read_top_per_query,
                'freshness': freshness,
                'render': render,
                'report_format': report_format,
                'follow_up_rounds': follow_up_rounds,
                'topic_profile': topic_profile.to_dict(),
                'query_plan': [item.to_dict() for item in query_plan],
            },
            'searches': [],
            'sources': [],
            'evidence': [],
            'claims': [],
            'selection_trace': [],
            'failures': [],
            'blocked_sources': [],
            'agent_loop': _agent_loop_state(
                planned_queries=query_plan,
                completed_plan_keys=completed_plan_keys,
                stop_reason='checkpoint_created',
                decisions=agent_loop_decisions,
                rounds=agent_loop_rounds,
            ),
            'phase_diagnostics': {
                'elapsed_seconds': elapsed_seconds(),
                'soft_timeout_seconds': soft_timeout_seconds,
                'phases': phase_diagnostics,
            },
            'checkpoint': {'completed_queries': [], 'remaining_queries': [item.query for item in query_plan]},
            'message': 'Deep research checkpoint created',
        }
        try:
            persistence = await asyncio.to_thread(save_research_run, 'deep_research', question, checkpoint_seed, status='in_progress')
            checkpoint_run_id = persistence['run_id']
        except (OSError, ValueError):
            checkpoint_run_id = None
        phase_diagnostics.append({'phase': 'checkpoint_create', 'elapsed_seconds': round(time.monotonic() - phase_started, 3), 'run_id': checkpoint_run_id})

    async def checkpoint_timeout_payload(*, message: str, remaining_queries: list[str], stop_reason: str) -> dict:
        sources, evidence, source_id_by_url = _remap_sources_and_evidence(source_by_url, evidence_by_key)
        remapped_selection_trace = _remap_selection_trace(selection_trace, source_id_by_url)
        source_selection_telemetry = _aggregate_child_source_selection_telemetry(searches, remapped_selection_trace, sources)
        payload = {
            'ok': bool(sources),
            'question': question,
            'status': 'in_progress',
            'run_id': checkpoint_run_id,
            'report_format': report_format,
            'strategy': {
                'mode': 'free_local_multi_search',
                'breadth': breadth,
                'read_top_per_query': read_top_per_query,
                'freshness': freshness,
                'render': render,
                'report_format': report_format,
                'follow_up_rounds': follow_up_rounds,
                'topic_profile': topic_profile.to_dict(),
                'query_plan': [item.to_dict() for item in query_plan],
                'auto_follow_up_plan': [item.to_dict() for item in auto_follow_up_plan],
            },
            'searches': searches,
            'sources': sources,
            'evidence': evidence,
            'selection_trace': remapped_selection_trace,
            'source_selection_telemetry': source_selection_telemetry,
            'failures': failures,
            'blocked_sources': blocked_sources,
            'checkpoint_warnings': checkpoint_warnings,
            'claims': [],
            'agent_loop': _agent_loop_state(
                planned_queries=query_plan + auto_follow_up_plan,
                completed_plan_keys=completed_plan_keys,
                stop_reason=stop_reason,
                decisions=agent_loop_decisions,
                rounds=agent_loop_rounds,
            ),
            'checkpoint': {
                'completed_queries': sorted(key[0] for key in completed_plan_keys),
                'remaining_queries': remaining_queries,
            },
            'phase_diagnostics': {
                'elapsed_seconds': elapsed_seconds(),
                'soft_timeout_seconds': soft_timeout_seconds,
                'phases': phase_diagnostics,
                'likely_timeout_phase': stop_reason,
            },
            'message': message,
        }
        if checkpoint_run_id:
            await _safe_checkpoint_update(checkpoint_run_id, payload, status='in_progress', warnings=checkpoint_warnings)
            payload['resume_tool_call'] = f'safe_resume_deep_research(run_id="{checkpoint_run_id}")'
        return payload

    for plan_item in query_plan:
        plan_key = _plan_key(plan_item.query, plan_item.site, plan_item.intent)
        if plan_key in completed_plan_keys:
            continue
        phase_started = time.monotonic()
        payload = await run_research_web(
            query=plan_item.query,
            max_results=max(6, read_top_per_query * 3),
            read_top=read_top_per_query,
            freshness=freshness,
            site=plan_item.site,
            render=render,
            report_format=report_format,
            persist=False,
            source_intent=plan_item.intent,
        )
        phase_diagnostics.append(
            {
                'phase': 'initial_search_fetch',
                'query': plan_item.query,
                'intent': plan_item.intent,
                'site': plan_item.site,
                'elapsed_seconds': round(time.monotonic() - phase_started, 3),
                'ok': bool(payload.get('ok')),
                'source_count': len(payload.get('sources', []) or []),
                'failure_count': len(payload.get('failures', []) or []),
                'provider': payload.get('search', {}).get('provider') if isinstance(payload.get('search'), dict) else None,
            }
        )
        _merge_child_research_payload(
            plan_item,
            payload,
            searches=searches,
            selection_trace=selection_trace,
            source_by_url=source_by_url,
            evidence_by_key=evidence_by_key,
            failures=failures,
            blocked_sources=blocked_sources,
        )
        completed_plan_keys.add(plan_key)
        agent_loop_decisions.append(
            {
                'round': 'initial',
                'agent': 'executor',
                'decision': 'searched_query',
                'query': plan_item.query,
                'intent': plan_item.intent,
                'site': plan_item.site,
                'source_intent': plan_item.intent,
                'ok': bool(payload.get('ok')),
                'source_count': len(payload.get('sources', []) or []),
                'failure_count': len(payload.get('failures', []) or []),
            }
        )
        if checkpoint_run_id:
            completed_queries = sorted(key[0] for key in completed_plan_keys)
            checkpoint_payload = {
                'ok': False,
                'question': question,
                'report_format': report_format,
                'strategy': {
                    'mode': 'free_local_multi_search',
                    'breadth': breadth,
                    'read_top_per_query': read_top_per_query,
                    'freshness': freshness,
                    'render': render,
                    'report_format': report_format,
                    'follow_up_rounds': follow_up_rounds,
                    'topic_profile': topic_profile.to_dict(),
                    'query_plan': [item.to_dict() for item in query_plan],
                },
                'searches': searches,
                'sources': list(source_by_url.values()),
                'evidence': [{key: value for key, value in item.items() if key != '_source_url'} for item in evidence_by_key.values()],
                'selection_trace': selection_trace,
                'failures': failures,
                'blocked_sources': blocked_sources,
                'checkpoint_warnings': checkpoint_warnings,
                'claims': [],
                'agent_loop': _agent_loop_state(
                    planned_queries=query_plan + auto_follow_up_plan,
                    completed_plan_keys=completed_plan_keys,
                    stop_reason='checkpoint_updated',
                    decisions=agent_loop_decisions,
                    rounds=agent_loop_rounds,
                ),
                'checkpoint': {
                    'completed_queries': sorted(completed_queries),
                    'remaining_queries': [
                        item.query for item in query_plan if _plan_key(item.query, item.site, item.intent) not in completed_plan_keys
                    ],
                },
                'message': 'Deep research checkpoint updated',
            }
            await _safe_checkpoint_update(checkpoint_run_id, checkpoint_payload, status='in_progress', warnings=checkpoint_warnings)
        if soft_timeout_reached():
            remaining = [
                item.query for item in query_plan if _plan_key(item.query, item.site, item.intent) not in completed_plan_keys
            ]
            return await checkpoint_timeout_payload(
                message='Deep research paused before LM Studio tool timeout. Resume the checkpoint to continue.',
                remaining_queries=remaining,
                stop_reason='soft_timeout_after_initial_query',
            )

    for round_index in range(follow_up_rounds):
        if soft_timeout_reached():
            return await checkpoint_timeout_payload(
                message='Deep research paused before follow-up planning to avoid LM Studio tool timeout. Resume the checkpoint to continue.',
                remaining_queries=[],
                stop_reason='soft_timeout_before_follow_up',
            )
        provisional_sources, provisional_evidence, provisional_source_id_by_url = _remap_sources_and_evidence(source_by_url, evidence_by_key)
        provisional_selection_trace = _remap_selection_trace(selection_trace, provisional_source_id_by_url)
        provisional_evidence_index = build_evidence_index(question, provisional_sources, provisional_evidence)
        provisional_claims = extract_claims_from_evidence(provisional_evidence)
        provisional_claim_support = build_claim_support_table(provisional_claims, provisional_evidence_index)
        provisional_blocked_sources = [failure for failure in failures if failure.get('blocked')]
        provisional_source_quality = _source_quality_summary(provisional_sources, provisional_selection_trace)
        provisional_source_selection_telemetry = _aggregate_child_source_selection_telemetry(
            searches,
            provisional_selection_trace,
            provisional_sources,
        )
        provisional_payload = {
            'ok': bool(provisional_sources),
            'question': question,
            'strategy': {
                'query_plan': [item.to_dict() for item in query_plan],
                'auto_follow_up_plan': [item.to_dict() for item in auto_follow_up_plan],
            },
            'searches': searches,
            'sources': provisional_sources,
            'evidence': provisional_evidence,
            'evidence_index': provisional_evidence_index,
            'citations': [item.get('citation') for item in provisional_evidence if item.get('citation')],
            'claims': provisional_claims,
            'claim_support': provisional_claim_support,
            'uncertainties': uncertainty_notes(
                claims=provisional_claims,
                failures=failures,
                blocked_sources=provisional_blocked_sources,
            ),
            'source_quality': provisional_source_quality,
            'source_selection_telemetry': provisional_source_selection_telemetry,
            'selection_trace': provisional_selection_trace,
            'failures': failures,
            'blocked_sources': provisional_blocked_sources,
        }
        provisional_payload['research_coverage'] = build_research_coverage(
            query_plan=query_plan + auto_follow_up_plan,
            searches=searches,
            selection_trace=provisional_selection_trace,
            source_quality=provisional_source_quality,
            query=question,
        )
        provisional_payload['source_freshness'] = build_freshness_summary(provisional_payload)
        provisional_payload['citation_validation'] = validate_citations(provisional_payload)
        provisional_payload['citation_audit'] = audit_citations(provisional_payload)
        provisional_payload['research_quality'] = assess_research_quality(provisional_payload)
        provisional_payload['recommended_next_searches'] = recommended_next_searches(provisional_payload)
        provisional_payload['final_answer_review'] = adversarial_final_answer_review(provisional_payload)
        observed_gaps = list(provisional_payload.get('research_quality', {}).get('gaps', []) or [])
        reviewer_issues = list(provisional_payload.get('final_answer_review', {}).get('issues', []) or [])
        follow_up_plan = _build_gap_follow_up_plan(
            question,
            provisional_payload,
            completed_plan_keys,
            limit=max(1, min(2, breadth - len(auto_follow_up_plan))),
        )
        agent_loop_rounds.append(
            {
                'round': round_index + 1,
                'quality_label': provisional_payload.get('research_quality', {}).get('label'),
                'quality_score': provisional_payload.get('research_quality', {}).get('score'),
                'source_count': len(provisional_sources),
                'unique_domain_count': provisional_payload.get('source_quality', {}).get('unique_domain_count'),
                'observed_gaps': observed_gaps,
                'reviewer_issue_count': provisional_payload.get('final_answer_review', {}).get('issue_count', 0),
                'reviewer_high_count': provisional_payload.get('final_answer_review', {}).get('high_count', 0),
                'planned_follow_up_count': len(follow_up_plan),
            }
        )
        agent_loop_decisions.append(
            {
                'round': round_index + 1,
                'agent': 'reviewer',
                'decision': 'reviewed_provisional_answer',
                'ok': provisional_payload.get('final_answer_review', {}).get('ok'),
                'issue_count': provisional_payload.get('final_answer_review', {}).get('issue_count', 0),
                'top_issues': [
                    {
                        'code': item.get('code'),
                        'severity': item.get('severity'),
                        'message': item.get('message'),
                    }
                    for item in reviewer_issues[:3]
                    if isinstance(item, dict)
                ],
            }
        )
        if not follow_up_plan:
            agent_loop_decisions.append(
                {
                    'round': round_index + 1,
                    'agent': 'planner',
                    'decision': 'stop_follow_up',
                    'reason': 'strong_enough' if provisional_payload.get('research_quality', {}).get('label') == 'strong' else 'low_value_followups',
                    'quality_label': provisional_payload.get('research_quality', {}).get('label'),
                }
            )
            break
        known_follow_up_keys = {_plan_key(item.query, item.site, item.intent) for item in auto_follow_up_plan}
        for item in follow_up_plan:
            key = _plan_key(item.query, item.site, item.intent)
            if key not in known_follow_up_keys:
                auto_follow_up_plan.append(item)
                known_follow_up_keys.add(key)
        agent_loop_decisions.append(
            {
                'round': round_index + 1,
                'agent': 'planner',
                'decision': 'planned_follow_up_queries',
                'planned_query_count': len(follow_up_plan),
                'queries': [item.to_dict() for item in follow_up_plan],
            }
        )
        for plan_item in follow_up_plan:
            plan_key = _plan_key(plan_item.query, plan_item.site, plan_item.intent)
            if plan_key in completed_plan_keys:
                continue
            before_source_count = len(source_by_url)
            phase_started = time.monotonic()
            payload = await run_research_web(
                query=plan_item.query,
                max_results=max(6, read_top_per_query * 3),
                read_top=read_top_per_query,
                freshness=freshness,
                site=plan_item.site,
                render=render,
                report_format=report_format,
                persist=False,
                source_intent=plan_item.intent,
            )
            phase_diagnostics.append(
                {
                    'phase': 'follow_up_search_fetch',
                    'query': plan_item.query,
                    'intent': plan_item.intent,
                    'site': plan_item.site,
                    'elapsed_seconds': round(time.monotonic() - phase_started, 3),
                    'ok': bool(payload.get('ok')),
                    'source_count': len(payload.get('sources', []) or []),
                    'failure_count': len(payload.get('failures', []) or []),
                    'provider': payload.get('search', {}).get('provider') if isinstance(payload.get('search'), dict) else None,
                }
            )
            _merge_child_research_payload(
                plan_item,
                payload,
                searches=searches,
                selection_trace=selection_trace,
                source_by_url=source_by_url,
                evidence_by_key=evidence_by_key,
                failures=failures,
                blocked_sources=blocked_sources,
            )
            completed_plan_keys.add(plan_key)
            new_source_count = len(source_by_url) - before_source_count
            agent_loop_decisions.append(
                {
                    'round': round_index + 1,
                    'agent': 'executor',
                    'decision': 'searched_follow_up_query',
                    'query': plan_item.query,
                    'intent': plan_item.intent,
                    'site': plan_item.site,
                    'source_intent': plan_item.intent,
                    'ok': bool(payload.get('ok')),
                    'source_count': len(payload.get('sources', []) or []),
                    'new_source_count': new_source_count,
                    'failure_count': len(payload.get('failures', []) or []),
                }
            )
            if soft_timeout_reached():
                return await checkpoint_timeout_payload(
                    message='Deep research paused after a follow-up search to avoid LM Studio tool timeout. Resume the checkpoint to continue.',
                    remaining_queries=[],
                    stop_reason='soft_timeout_after_follow_up_query',
                )
        round_new_source_count = sum(
            int(item.get('new_source_count') or 0)
            for item in agent_loop_decisions
            if item.get('round') == round_index + 1 and item.get('decision') == 'searched_follow_up_query'
        )
        agent_loop_rounds[-1]['new_source_count'] = round_new_source_count
        if round_new_source_count == 0:
            no_new_follow_up_rounds += 1
        else:
            no_new_follow_up_rounds = 0
        if no_new_follow_up_rounds:
            agent_loop_decisions.append(
                {
                    'round': round_index + 1,
                    'agent': 'planner',
                    'decision': 'stop_follow_up',
                    'reason': 'no_new_sources',
                    'new_source_count': round_new_source_count,
                }
            )
            break
        if checkpoint_run_id:
            checkpoint_payload = {
                'ok': False,
                'question': question,
                'report_format': report_format,
                'strategy': {
                    'mode': 'free_local_multi_search',
                    'breadth': breadth,
                    'read_top_per_query': read_top_per_query,
                    'freshness': freshness,
                    'render': render,
                    'report_format': report_format,
                    'follow_up_rounds': follow_up_rounds,
                    'topic_profile': topic_profile.to_dict(),
                    'query_plan': [item.to_dict() for item in query_plan],
                    'auto_follow_up_plan': [item.to_dict() for item in auto_follow_up_plan],
                },
                'searches': searches,
                'sources': list(source_by_url.values()),
                'evidence': [{key: value for key, value in item.items() if key != '_source_url'} for item in evidence_by_key.values()],
                'selection_trace': selection_trace,
                'failures': failures,
                'blocked_sources': blocked_sources,
                'checkpoint_warnings': checkpoint_warnings,
                'claims': [],
                'agent_loop': _agent_loop_state(
                    planned_queries=query_plan + auto_follow_up_plan,
                    completed_plan_keys=completed_plan_keys,
                    observed_gaps=observed_gaps,
                    stop_reason='checkpoint_updated',
                    decisions=agent_loop_decisions,
                    rounds=agent_loop_rounds,
                ),
                'checkpoint': {
                    'completed_queries': sorted(key[0] for key in completed_plan_keys),
                    'remaining_queries': [],
                    'follow_up_round': round_index + 1,
                },
                'message': 'Deep research follow-up checkpoint updated',
            }
            await _safe_checkpoint_update(checkpoint_run_id, checkpoint_payload, status='in_progress', warnings=checkpoint_warnings)

    sources, evidence, source_id_by_url = _remap_sources_and_evidence(source_by_url, evidence_by_key)
    evidence_index = build_evidence_index(question, sources, evidence)
    claims = extract_claims_from_evidence(evidence)
    claim_support = build_claim_support_table(claims, evidence_index)
    claim_review = await review_claim_contradictions(claims)
    remapped_selection_trace = _remap_selection_trace(selection_trace, source_id_by_url)
    source_quality = _source_quality_summary(sources, remapped_selection_trace)
    source_selection_telemetry = _aggregate_child_source_selection_telemetry(searches, remapped_selection_trace, sources)
    payload = {
        'ok': bool(sources),
        'question': question,
        'strategy': {
            'mode': 'free_local_multi_search',
            'search_backend': 'local SearXNG first, free web fallbacks second',
            'breadth': breadth,
            'read_top_per_query': read_top_per_query,
            'freshness': freshness,
            'render': render,
            'report_format': report_format,
            'follow_up_rounds': follow_up_rounds,
            'topic_profile': topic_profile.to_dict(),
            'query_plan': [item.to_dict() for item in query_plan],
            'auto_follow_up_plan': [item.to_dict() for item in auto_follow_up_plan],
            'search_backend_summary': _search_backend_summary(searches),
        },
        'searches': searches,
        'sources': sources,
        'evidence': evidence,
        'evidence_index': evidence_index,
        'citations': [item.get('citation') for item in evidence if item.get('citation')],
        'claims': claims,
        'claim_support': claim_support,
        'claim_review': claim_review,
        'uncertainties': uncertainty_notes(claims=claims, failures=failures, blocked_sources=blocked_sources),
        'recent_changes': recent_change_notes(evidence),
        'source_quality': source_quality,
        'source_selection_telemetry': source_selection_telemetry,
        'checkpoint_warnings': checkpoint_warnings,
        'selection_trace': remapped_selection_trace,
        'failures': failures,
        'blocked_sources': blocked_sources,
        'message': 'Deep research completed with sources' if sources else 'Deep research found no readable sources',
    }
    payload['research_coverage'] = build_research_coverage(
        query_plan=query_plan + auto_follow_up_plan,
        searches=searches,
        selection_trace=remapped_selection_trace,
        source_quality=source_quality,
        query=question,
    )
    payload['source_freshness'] = build_freshness_summary(payload)
    payload['citation_validation'] = validate_citations(payload)
    payload['citation_audit'] = audit_citations(payload)
    payload['research_quality'] = assess_research_quality(payload)
    payload['recommended_next_searches'] = recommended_next_searches(payload)
    final_gaps = list(payload.get('research_quality', {}).get('gaps', []) or [])
    if not sources:
        stop_reason = 'no_new_sources'
    elif len(blocked_sources) >= 3 and len(sources) < 3:
        stop_reason = 'blocked_too_often'
    elif payload.get('research_quality', {}).get('label') == 'strong':
        stop_reason = 'strong_enough'
    elif agent_loop_decisions and agent_loop_decisions[-1].get('reason') in {'low_value_followups', 'no_new_sources'}:
        stop_reason = str(agent_loop_decisions[-1].get('reason'))
    else:
        stop_reason = 'max_rounds'
    payload['agent_loop'] = _agent_loop_state(
        planned_queries=query_plan + auto_follow_up_plan,
        completed_plan_keys=completed_plan_keys,
        observed_gaps=final_gaps,
        stop_reason=stop_reason,
        decisions=agent_loop_decisions,
        rounds=agent_loop_rounds,
    )
    payload['phase_diagnostics'] = {
        'elapsed_seconds': elapsed_seconds(),
        'soft_timeout_seconds': soft_timeout_seconds,
        'phases': phase_diagnostics,
        'likely_timeout_phase': 'final_report_synthesis' if soft_timeout_reached() else None,
    }
    if soft_timeout_reached():
        return await checkpoint_timeout_payload(
            message='Deep research paused before report synthesis to avoid LM Studio tool timeout. Resume the checkpoint to finish the report.',
            remaining_queries=[],
            stop_reason='soft_timeout_before_report_synthesis',
        )
    phase_started = time.monotonic()
    await finalize_report_payload(payload, report_format=report_format)
    phase_diagnostics.append({'phase': 'report_synthesis', 'elapsed_seconds': round(time.monotonic() - phase_started, 3)})
    payload['phase_diagnostics'] = {
        'elapsed_seconds': elapsed_seconds(),
        'soft_timeout_seconds': soft_timeout_seconds,
        'phases': phase_diagnostics,
        'likely_timeout_phase': None,
    }
    try:
        if checkpoint_run_id:
            persistence = await asyncio.to_thread(update_research_run, checkpoint_run_id, payload, status='completed')
        else:
            persistence = await asyncio.to_thread(save_research_run, 'deep_research', question, payload)
        if not (persistence.get('ok') or persistence.get('saved')):
            raise ValueError(str(persistence.get('message') or 'Could not persist deep research run'))
        payload.update(
            {
                'run_id': persistence['run_id'],
                'run_path': persistence['run_path'],
                'final_report_path': persistence.get('final_report_path'),
                'persistence': persistence,
            }
        )
    except (OSError, ValueError) as exc:
        payload['persistence'] = {'saved': False, 'message': f'Could not save research run: {exc}'}
    return payload


@mcp.tool()
def list_research_runs(limit: int = 20) -> dict:
    '''List recent persisted research runs so a model can continue or audit prior work.'''
    return run_list_research_runs(limit=limit)


@mcp.tool()
def safe_work_loop_status(request: str) -> dict:
    '''Low-risk one-parameter status reader for unattended work-loop artifacts.'''
    try:
        parsed = _parse_safe_work_loop_status_request(request)
        options = parsed['options']
        values = [str(value).strip() for value in parsed['values'] if str(value).strip()]
        try:
            limit = int(str(options.get('limit') or '5').strip())
        except ValueError:
            return {'ok': False, 'message': 'limit must be an integer.'}
        limit = max(1, min(limit, 20))
        selector = str(options.get('selector') or 'latest').strip() or 'latest'
        loops = collect_work_loops(MCP_WORK_LOOP_ROOT, limit=20)
        if values:
            wanted = set(values)
            selected = [item for item in loops if item.get('id') in wanted]
            selector = 'explicit'
        elif selector == 'active':
            selected = [item for item in loops if item.get('in_progress')][:limit]
        elif selector == 'stale':
            selected = [item for item in loops if item.get('stale')][:limit]
        elif selector in {'latest', 'recent', 'status', 'summary', 'loops'}:
            selected = loops[:limit]
            selector = 'latest'
        else:
            selected = [item for item in loops if selector.lower() in str(item.get('id') or '').lower()][:limit]
        for item in selected:
            events_path = Path(str(item.get('events_path') or ''))
            item['event_tail'] = _tail_text_file(events_path, limit=5)
        return {
            'ok': True,
            'tool': 'safe_work_loop_status',
            'selector': selector,
            'loop_count': len(selected),
            'available_loop_count': len(loops),
            'work_loop_root': str(MCP_WORK_LOOP_ROOT),
            'loops': selected,
            'message': 'No work-loop artifacts matched the request.' if not selected else 'Work-loop status loaded.',
        }
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': f'Could not read work-loop status: {exc}'}


@mcp.tool()
def safe_cleanup_work_loops(request: str) -> dict:
    '''Low-risk one-parameter stale work-loop cleanup. Previews by default; requires apply=true to write.'''
    try:
        parsed = _parse_safe_work_loop_status_request(request)
        options = parsed['options']
        values = [str(value).strip() for value in parsed['values'] if str(value).strip()]
        apply = _parse_bool_text(options.get('apply'), default=False)
        include_legacy_missing_pid = _parse_bool_text(options.get('include_legacy_missing_pid'), default=False)
        review_failed = _parse_bool_text(options.get('review_failed'), default=False) or _parse_bool_text(
            options.get('review-failed'), default=False
        )
        if apply and not values:
            return {
                'ok': False,
                'message': 'Cleanup/review writes require an explicit loop_id. Preview first, then retry with loop_id: <id> and apply=true.',
            }
        try:
            limit = int(str(options.get('limit') or '20').strip())
        except ValueError:
            return {'ok': False, 'message': 'limit must be an integer.'}
        result = cleanup_stale_work_loops(
            MCP_WORK_LOOP_ROOT,
            apply=apply,
            limit=limit,
            loop_ids=values,
            include_legacy_missing_pid=include_legacy_missing_pid,
            review_failed=review_failed,
            review_note=options.get('note') or options.get('review_note'),
        )
        result['tool'] = 'safe_cleanup_work_loops'
        result['dry_run'] = not apply
        return result
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': f'Could not clean up work loops: {exc}'}


@mcp.tool()
def safe_submit_research_job(request: str) -> dict:
    '''Low-risk one-parameter research job submitter. Previews by default; requires submit=true or apply=true to queue.'''
    try:
        parsed = _parse_safe_research_job_request(request)
        options = parsed['options']
        research_request = str(parsed['request'] or '').strip()
        if not research_request:
            return {
                'ok': False,
                'message': 'Research job request is required.',
                'expected_format': 'question text plus optional lines like profile=careful, priority=2, tag=market, submit=true',
            }
        try:
            profile = get_work_profile(str(options.get('profile') or 'careful'))
        except ValueError as exc:
            return {'ok': False, 'message': str(exc)}
        try:
            priority = int(str(options.get('priority') or '0').strip())
        except ValueError:
            return {'ok': False, 'message': 'priority must be an integer.'}
        tags = [tag for tag in parsed['tags'] if tag]
        submit = _research_job_apply_requested(options)
        planned_job = {
            'request': research_request,
            'profile': profile.name,
            'priority': priority,
            'tags': tags,
            'status': 'queued',
        }
        if not submit:
            return {
                'ok': True,
                'tool': 'safe_submit_research_job',
                'dry_run': True,
                'planned_job': planned_job,
                'research_jobs_root': str(MCP_RESEARCH_JOBS_ROOT),
                'message': 'Preview only. Add submit=true or apply=true to queue this research job.',
            }
        result = create_research_job(
            MCP_RESEARCH_JOBS_ROOT,
            request=research_request,
            profile=profile.name,
            priority=priority,
            tags=tags,
        )
        result['tool'] = 'safe_submit_research_job'
        result['dry_run'] = False
        result['research_jobs_root'] = str(MCP_RESEARCH_JOBS_ROOT)
        return result
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': f'Could not submit research job: {exc}'}


@mcp.tool()
def safe_research_job_status(request: str) -> dict:
    '''Low-risk one-parameter research job status reader for queued, running, completed, or cancelled jobs.'''
    try:
        parsed = _parse_safe_research_job_request(request)
        options = parsed['options']
        values = [str(value).strip() for value in parsed['values'] if str(value).strip()]
        try:
            limit = int(str(options.get('limit') or '10').strip())
        except ValueError:
            return {'ok': False, 'message': 'limit must be an integer.'}
        limit = max(1, min(limit, 50))
        if values:
            jobs = []
            failures = []
            for job_id in values:
                loaded = load_research_job(MCP_RESEARCH_JOBS_ROOT, job_id)
                if loaded.get('ok'):
                    jobs.append(loaded['job'])
                else:
                    failures.append({'job_id': job_id, 'message': loaded.get('message')})
            return {
                'ok': not failures,
                'tool': 'safe_research_job_status',
                'selector': 'explicit',
                'job_count': len(jobs),
                'research_jobs_root': str(MCP_RESEARCH_JOBS_ROOT),
                'jobs': jobs,
                'failures': failures,
            }
        status = str(options.get('status') or '').strip() or None
        if status == 'canceled':
            status = 'cancelled'
        result = list_research_jobs(MCP_RESEARCH_JOBS_ROOT, status=status, limit=limit)
        result['tool'] = 'safe_research_job_status'
        result['selector'] = status or str(options.get('selector') or 'latest')
        result['research_jobs_root'] = str(MCP_RESEARCH_JOBS_ROOT)
        result['message'] = 'No research jobs matched the request.' if not result.get('jobs') else 'Research job status loaded.'
        return result
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': f'Could not read research job status: {exc}'}


@mcp.tool()
def safe_cancel_research_job(request: str) -> dict:
    '''Low-risk one-parameter research job canceller. Requires an explicit job_id.'''
    try:
        parsed = _parse_safe_research_job_request(request)
        values = [str(value).strip() for value in parsed['values'] if str(value).strip()]
        if not values:
            return {'ok': False, 'message': 'Cancel requires an explicit job_id: <id>.'}
        results = []
        failures = []
        for job_id in values:
            result = update_research_job(
                MCP_RESEARCH_JOBS_ROOT,
                job_id,
                status='cancelled',
                event='cancelled',
                message='Cancelled through safe_cancel_research_job.',
            )
            if result.get('ok'):
                results.append(result['job'])
            else:
                failures.append({'job_id': job_id, 'message': result.get('message')})
        return {
            'ok': not failures,
            'tool': 'safe_cancel_research_job',
            'cancelled_count': len(results),
            'research_jobs_root': str(MCP_RESEARCH_JOBS_ROOT),
            'jobs': results,
            'failures': failures,
        }
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': f'Could not cancel research job: {exc}'}


@mcp.tool()
def safe_research_checkpoint_status(request: str) -> dict:
    '''Low-risk one-parameter status reader for resumable deep_research checkpoints.'''
    try:
        parsed = _parse_safe_checkpoint_request(request)
        options = parsed['options']
        values = [str(value).strip() for value in parsed['values'] if str(value).strip()]
        try:
            limit = int(str(options.get('limit') or '10').strip())
        except ValueError:
            return {'ok': False, 'message': 'limit must be an integer.'}
        limit = max(1, min(limit, 50))
        if values:
            checkpoints = []
            failures = []
            for run_id in values:
                loaded = load_research_run(run_id, root=MCP_RESEARCH_RUNS_ROOT)
                metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
                if loaded.get('ok') and metadata.get('kind') == 'deep_research' and metadata.get('status') in {'in_progress', 'interrupted'}:
                    checkpoints.append(
                        {
                            'run_id': run_id,
                            'kind': metadata.get('kind'),
                            'status': metadata.get('status'),
                            'created_at': metadata.get('created_at'),
                            'updated_at': metadata.get('updated_at'),
                            'query': metadata.get('query'),
                            'resume_tool_call': f'safe_resume_deep_research(run_id="{run_id}")',
                            'checkpoint': loaded.get('payload', {}).get('checkpoint') if isinstance(loaded.get('payload'), dict) else None,
                        }
                    )
                else:
                    failures.append({'run_id': run_id, 'message': loaded.get('message') or 'Run is not a resumable checkpoint.'})
            return {
                'ok': not failures,
                'tool': 'safe_research_checkpoint_status',
                'selector': 'explicit',
                'checkpoint_count': len(checkpoints),
                'research_runs_root': str(MCP_RESEARCH_RUNS_ROOT),
                'checkpoints': checkpoints,
                'failures': failures,
            }
        status = str(options.get('status') or '').strip() or None
        result = list_research_checkpoints(status=status, limit=limit, root=MCP_RESEARCH_RUNS_ROOT)
        result['tool'] = 'safe_research_checkpoint_status'
        result['selector'] = status or str(options.get('selector') or 'latest')
        result['research_runs_root'] = str(MCP_RESEARCH_RUNS_ROOT)
        result['message'] = 'No research checkpoints matched the request.' if not result.get('checkpoints') else 'Research checkpoints loaded.'
        for checkpoint in result.get('checkpoints', []) or []:
            if checkpoint.get('run_id'):
                checkpoint['resume_tool_call'] = f'safe_resume_deep_research(run_id="{checkpoint["run_id"]}")'
        return result
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': f'Could not read research checkpoint status: {exc}'}


@mcp.tool()
def safe_interrupt_research_checkpoints(request: str) -> dict:
    '''Low-risk one-parameter checkpoint interrupter. Previews by default; requires run_id and apply=true to write.'''
    try:
        parsed = _parse_safe_checkpoint_request(request)
        options = parsed['options']
        values = [str(value).strip() for value in parsed['values'] if str(value).strip()]
        apply = _parse_bool_text(options.get('apply'), default=False)
        if not values:
            return {'ok': False, 'message': 'Interrupt requires explicit run_id values. Preview checkpoint status first.'}
        if not apply:
            previews = []
            failures = []
            for run_id in values:
                loaded = load_research_run(run_id, root=MCP_RESEARCH_RUNS_ROOT)
                metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
                if loaded.get('ok'):
                    previews.append(
                        {
                            'run_id': run_id,
                            'kind': metadata.get('kind'),
                            'status': metadata.get('status'),
                            'will_interrupt': metadata.get('kind') == 'deep_research' and metadata.get('status') == 'in_progress',
                            'resume_supported_after_interrupt': metadata.get('kind') == 'deep_research',
                        }
                    )
                else:
                    failures.append({'run_id': run_id, 'message': loaded.get('message')})
            return {
                'ok': not failures,
                'tool': 'safe_interrupt_research_checkpoints',
                'dry_run': True,
                'research_runs_root': str(MCP_RESEARCH_RUNS_ROOT),
                'previews': previews,
                'failures': failures,
                'message': 'Preview only. Add apply=true to mark interruptible checkpoints as interrupted.',
            }
        results = []
        failures = []
        for run_id in values:
            result = interrupt_research_checkpoint(
                run_id,
                root=MCP_RESEARCH_RUNS_ROOT,
                message='Marked interrupted through safe_interrupt_research_checkpoints.',
            )
            if result.get('ok'):
                results.append(result)
            else:
                failures.append({'run_id': run_id, 'message': result.get('message')})
        return {
            'ok': not failures,
            'tool': 'safe_interrupt_research_checkpoints',
            'dry_run': False,
            'interrupted_count': sum(1 for item in results if not item.get('already_interrupted')),
            'research_runs_root': str(MCP_RESEARCH_RUNS_ROOT),
            'results': results,
            'failures': failures,
        }
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': f'Could not interrupt research checkpoints: {exc}'}


@mcp.tool()
def safe_list_research_runs(request: str) -> dict:
    '''Low-risk one-parameter recent-run list. The request text is only a label; returns 10 recent runs.'''
    return run_list_research_runs(limit=10)


@mcp.tool()
def safe_find_research_runs(query: str) -> dict:
    '''Low-risk one-parameter prior-run search for follow-up research.'''
    return run_find_research_runs(query=query, limit=5)


@mcp.tool()
def safe_research_context(query: str) -> dict:
    '''Low-risk one-parameter automatic prior-context loader for fresh-chat follow-ups.'''
    return build_research_context(query=query, limit=3, root=MCP_RESEARCH_RUNS_ROOT)


@mcp.tool()
def find_research_runs(query: str, limit: int = 5) -> dict:
    '''Find prior research runs relevant to a query so follow-ups do not require a pasted run_id.'''
    return run_find_research_runs(query=query, limit=limit)


@mcp.tool()
def safe_get_research_run(run_id: str) -> dict:
    '''Low-risk one-parameter persisted-run reader with compact output.'''
    saved = load_research_run(run_id)
    metadata = saved.get('run') if isinstance(saved.get('run'), dict) else {}
    payload = saved.get('payload') if isinstance(saved.get('payload'), dict) else {}
    return {
        'ok': saved.get('ok'),
        'run': saved.get('run'),
        'suggested_actions': _run_action_hints(metadata),
        'payload': compact_research_payload(payload),
    }


@mcp.tool()
def safe_export_research_run(request: str) -> dict:
    '''Low-risk one-parameter exporter for saved research runs. Accepts run_id, latest=N, or find=query.'''
    try:
        parsed = _parse_safe_packaging_request(request)
        options = parsed['options']
        selected = _selected_run_ids_from_safe_request(parsed)
        if not selected.get('ok'):
            return selected
        redact = _profile_redaction(options, default=False)
        zip_bundle = _parse_bool_text(options.get('zip'), default=False)
        if _packaging_dry_run(options):
            selected_run_ids = _dedupe_run_ids(selected.get('run_ids', []) or [])
            if not selected_run_ids:
                return {
                    'ok': False,
                    'dry_run': True,
                    'tool': 'safe_export_research_run',
                    'selector': selected.get('selector'),
                    'run_ids': [],
                    'run_count': 0,
                    'redacted': redact,
                    'zip_bundle': zip_bundle,
                    'planned_output_root': str(MCP_EXPORT_ROOT),
                    'message': 'Preview selected no research runs. No export files were written.',
                }
            return {
                'ok': True,
                'dry_run': True,
                'tool': 'safe_export_research_run',
                'selector': selected.get('selector'),
                'run_ids': selected_run_ids,
                'run_count': len(selected_run_ids),
                'redacted': redact,
                'zip_bundle': zip_bundle,
                'planned_output_root': str(MCP_EXPORT_ROOT),
                'message': 'Preview only. No export files were written.',
            }
        output_dir = MCP_EXPORT_ROOT / uuid.uuid4().hex[:10]
        run_ids = _dedupe_run_ids(selected.get('run_ids', []) or [])
        if len(run_ids) == 1:
            result = export_research_run(str(run_ids[0]), output_dir=output_dir, zip_bundle=zip_bundle, redact=redact)
        else:
            result = export_research_runs(
                [str(run_id) for run_id in run_ids],
                output_dir=output_dir,
                zip_bundle=zip_bundle,
                selector=str(selected.get('selector') or 'safe_export'),
                redact=redact,
            )
        result['tool'] = 'safe_export_research_run'
        result['selector'] = selected.get('selector')
        result['run_ids'] = run_ids
        result['run_count'] = len(run_ids)
        return result
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': f'Could not export research run: {exc}'}


@mcp.tool()
def safe_build_source_pack(request: str) -> dict:
    '''Low-risk one-parameter source-pack builder. Defaults to redacted output for sharing.'''
    try:
        parsed = _parse_safe_packaging_request(request)
        options = parsed['options']
        profile_name = str(options.get('profile') or '')
        default_latest = get_work_profile(profile_name).source_pack_latest if profile_name else 1
        selected = _selected_run_ids_from_safe_request(parsed, default_latest=default_latest)
        if not selected.get('ok'):
            return selected
        redact = _profile_redaction(options, default=True)
        if _packaging_dry_run(options):
            selected_run_ids = _dedupe_run_ids(selected.get('run_ids', []) or [])
            if not selected_run_ids:
                return {
                    'ok': False,
                    'dry_run': True,
                    'tool': 'safe_build_source_pack',
                    'selector': selected.get('selector'),
                    'run_ids': [],
                    'run_count': 0,
                    'redacted': redact,
                    'planned_output_root': str(MCP_SOURCE_PACK_ROOT),
                    'message': 'Preview selected no research runs. No source-pack files were written.',
                }
            return {
                'ok': True,
                'dry_run': True,
                'tool': 'safe_build_source_pack',
                'selector': selected.get('selector'),
                'run_ids': selected_run_ids,
                'run_count': len(selected_run_ids),
                'redacted': redact,
                'planned_output_root': str(MCP_SOURCE_PACK_ROOT),
                'message': 'Preview only. No source-pack files were written.',
            }
        output_dir = MCP_SOURCE_PACK_ROOT / uuid.uuid4().hex[:10]
        run_ids = _dedupe_run_ids(selected.get('run_ids', []) or [])
        pack = collect_source_pack(run_ids, redact=redact)
        result = write_source_pack(pack, output_dir)
        result['tool'] = 'safe_build_source_pack'
        result['selector'] = selected.get('selector')
        result['run_ids'] = run_ids
        result['run_count'] = len(run_ids)
        return result
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': f'Could not build source pack: {exc}'}


@mcp.tool()
def get_research_run(run_id: str, include_full: bool = False) -> dict:
    '''Load one persisted research run by run_id, including sources, evidence, claims, and metadata.'''
    saved = load_research_run(run_id)
    if include_full or not settings.mcp_compact_results:
        return saved
    payload = saved.get('payload') if isinstance(saved.get('payload'), dict) else {}
    return {'ok': saved.get('ok'), 'run': saved.get('run'), 'payload': compact_research_payload(payload)}


@mcp.tool()
def invalidate_research_cache(max_age_seconds: int | None = None, content_hash: str | None = None, clear_all: bool = False) -> dict:
    '''Invalidate cached search/read results by age, content hash, or full cache clear.'''
    removed = 0
    actions = []
    if clear_all:
        removed += cache.clear()
        actions.append('clear_all')
    if max_age_seconds is not None:
        max_age_seconds = max(0, max_age_seconds)
        removed += cache.invalidate_older_than(max_age_seconds)
        actions.append('max_age_seconds')
    if content_hash:
        removed += cache.invalidate_content_hash(content_hash)
        actions.append('content_hash')
    return {
        'ok': True,
        'removed': removed,
        'actions': actions,
        'cache': cache.stats(),
    }


@mcp.tool()
async def resume_deep_research(run_id: str) -> dict:
    '''Resume an interrupted deep_research checkpoint from its next unfinished planned query.'''
    saved = load_research_run(run_id)
    if not saved.get('ok'):
        return saved
    metadata = saved.get('run') if isinstance(saved.get('run'), dict) else {}
    payload = saved.get('payload') if isinstance(saved.get('payload'), dict) else None
    if not isinstance(payload, dict):
        return {'ok': False, 'run_id': run_id, 'message': 'Research run payload is missing or invalid.'}
    if metadata.get('kind') != 'deep_research':
        return {'ok': False, 'run_id': run_id, 'message': 'Only deep_research runs can be resumed.'}
    if metadata.get('status') == 'completed':
        return {
            'ok': True,
            'run_id': run_id,
            'message': 'Research run is already completed.',
            'payload': compact_research_payload(payload),
        }
    strategy = payload.get('strategy') if isinstance(payload.get('strategy'), dict) else {}
    question = str(payload.get('question') or metadata.get('query') or '')
    if not question:
        return {'ok': False, 'run_id': run_id, 'message': 'Research run is missing its original question.'}
    result = await _run_deep_research(
        question,
        breadth=int(strategy.get('breadth') or 4),
        read_top_per_query=int(strategy.get('read_top_per_query') or 2),
        freshness=strategy.get('freshness'),
        render=bool(strategy.get('render', False)),
        report_format=strategy.get('report_format') or payload.get('report_format') or 'long_report',
        follow_up_rounds=int(strategy.get('follow_up_rounds') or 0),
        checkpoint_run_id=run_id,
        resume_payload=payload,
    )
    return compact_research_payload(result)


def _source_url(source: dict) -> str | None:
    return source.get('final_url') or source.get('url')


async def _merge_continuation_payload(
    parent_payload: dict,
    follow_up_query: str,
    follow_up: dict,
    parent_run_id: str,
    *,
    report_format: str = 'long_report',
) -> dict:
    report_format = normalize_report_format(report_format)
    sources = []
    source_id_by_url = {}
    for source in list(parent_payload.get('sources', []) or []) + list(follow_up.get('sources', []) or []):
        url = _source_url(source)
        if not url or url in source_id_by_url:
            continue
        remapped = dict(source)
        remapped['source_id'] = len(sources) + 1
        source_id_by_url[url] = remapped['source_id']
        if source.get('url'):
            source_id_by_url[source['url']] = remapped['source_id']
        if source.get('final_url'):
            source_id_by_url[source['final_url']] = remapped['source_id']
        sources.append(remapped)

    evidence_by_key = {}
    for item in list(parent_payload.get('evidence', []) or []) + list(follow_up.get('evidence', []) or []):
        item_url = item.get('url')
        text = item.get('text') or item.get('quote') or item.get('citation')
        key = (item_url, text)
        if key in evidence_by_key:
            continue
        remapped = dict(item)
        source_id = source_id_by_url.get(item_url)
        if source_id is not None:
            remapped['source_id'] = source_id
            if remapped.get('char_range'):
                start, end = remapped['char_range']
                remapped['citation'] = f'source:{source_id}[{start}:{end}]'
        evidence_by_key[key] = remapped
    evidence = list(evidence_by_key.values())
    evidence.sort(key=lambda item: (item.get('rank', 999), item.get('source_id', 999)))

    failures = list(parent_payload.get('failures', []) or []) + list(follow_up.get('failures', []) or [])
    blocked_sources = [failure for failure in failures if failure.get('blocked')]
    selection_trace = list(parent_payload.get('selection_trace', []) or [])
    for item in follow_up.get('selection_trace', []) or []:
        trace_item = {'continuation_query': follow_up_query, **item}
        trace_url = trace_item.get('recovered_url') or trace_item.get('final_url') or trace_item.get('url')
        source_id = source_id_by_url.get(trace_url)
        if source_id is not None and trace_item.get('source_id') is not None:
            trace_item['source_id'] = source_id
        selection_trace.append(trace_item)
    parent_question = parent_payload.get('question') or parent_payload.get('query') or ''
    claims = extract_claims_from_evidence(evidence)
    evidence_index = build_evidence_index(str(parent_question), sources, evidence)
    claim_support = build_claim_support_table(claims, evidence_index)
    claim_review = await review_claim_contradictions(claims)
    searches = list(parent_payload.get('searches', []) or []) + [
        {
            'query': follow_up_query,
            'intent': 'follow_up',
            'ok': follow_up.get('ok', False),
            'provider': follow_up.get('search', {}).get('provider'),
            'result_count': len(follow_up.get('search', {}).get('results', [])),
            'source_count': len(follow_up.get('sources', [])),
            'selection_trace_count': len(follow_up.get('selection_trace', [])),
            'message': follow_up.get('message'),
        }
    ]
    source_quality = _source_quality_summary(sources, selection_trace)
    payload = {
        'ok': bool(sources),
        'question': f'{parent_question} | follow-up: {follow_up_query}'.strip(' |'),
        'parent_run_id': parent_run_id,
        'follow_up_query': follow_up_query,
        'strategy': {
            'mode': 'continued_research_run',
            'parent_run_id': parent_run_id,
            'follow_up_query': follow_up_query,
            'report_format': report_format,
        },
        'searches': searches,
        'sources': sources,
        'evidence': evidence,
        'evidence_index': evidence_index,
        'citations': [item.get('citation') for item in evidence if item.get('citation')],
        'claims': claims,
        'claim_support': claim_support,
        'claim_review': claim_review,
        'uncertainties': uncertainty_notes(claims=claims, failures=failures, blocked_sources=blocked_sources),
        'recent_changes': recent_change_notes(evidence),
        'source_quality': source_quality,
        'selection_trace': selection_trace,
        'failures': failures,
        'blocked_sources': blocked_sources,
        'message': 'Continued research completed with sources' if sources else 'Continuation found no readable sources',
    }
    payload['research_coverage'] = build_research_coverage(
        query_plan=[
            {
                'query': parent_question,
                'intent': 'parent_run',
                'rationale': 'Previously completed parent research run.',
            },
            {
                'query': follow_up_query,
                'intent': 'follow_up',
                'rationale': 'User-requested continuation query.',
            },
        ],
        searches=searches,
        selection_trace=selection_trace,
        source_quality=source_quality,
        query=payload['question'],
    )
    payload['source_freshness'] = build_freshness_summary(payload)
    payload['citation_validation'] = validate_citations(payload)
    payload['citation_audit'] = audit_citations(payload)
    payload['research_quality'] = assess_research_quality(payload)
    payload['recommended_next_searches'] = recommended_next_searches(payload)
    await finalize_report_payload(payload, report_format=report_format)
    return payload


@mcp.tool()
async def continue_research_run(
    run_id: str,
    follow_up_query: str,
    max_results: int = 8,
    read_top: int = 3,
    freshness: str | None = None,
    render: bool = False,
    report_format: str = 'long_report',
) -> dict:
    '''Continue a persisted research run with a focused follow-up query and save a linked child run.'''
    report_format = normalize_report_format(report_format)
    parent = load_research_run(run_id)
    if not parent.get('ok'):
        return parent
    parent_payload = parent.get('payload')
    if not isinstance(parent_payload, dict):
        return {'ok': False, 'run_id': run_id, 'message': 'Research run payload is missing or invalid.'}
    follow_up = await run_research_web(
        query=follow_up_query,
        max_results=max_results,
        read_top=read_top,
        freshness=freshness,
        render=render,
        report_format=report_format,
        persist=False,
        source_intent='follow_up',
    )
    payload = await _merge_continuation_payload(parent_payload, follow_up_query, follow_up, run_id, report_format=report_format)
    try:
        persistence = await asyncio.to_thread(
            save_research_run,
            'continued_research',
            follow_up_query,
            payload,
            parent_run_id=run_id,
        )
        payload.update(
            {
                'run_id': persistence['run_id'],
                'run_path': persistence['run_path'],
                'final_report_path': persistence.get('final_report_path'),
                'persistence': persistence,
            }
        )
    except (OSError, ValueError) as exc:
        payload['persistence'] = {'saved': False, 'message': f'Could not save research run: {exc}'}
    return compact_research_payload(payload)


@mcp.tool()
async def safe_continue_research_run(request: str) -> dict:
    '''Low-risk one-parameter follow-up continuation. First line is run_id; remaining lines are the query.'''
    try:
        run_id, follow_up_query = _parse_safe_continue_input(request)
    except ValueError as exc:
        return {
            'ok': False,
            'message': str(exc),
            'expected_format': 'run_id on first line, follow-up query on following line(s)',
        }
    return await continue_research_run(
        run_id=run_id,
        follow_up_query=follow_up_query,
        max_results=8,
        read_top=2,
        freshness=None,
        render=False,
        report_format='executive_brief',
    )


_prune_mcp_tools()


if __name__ == '__main__':
    logger.info(
        'Starting MCP server transport=%s host=%s port=%s streamable_http_path=%s sse_path=%s tools=%s tool_profile=%s advanced_tools=%s',
        settings.mcp_transport,
        settings.mcp_host,
        settings.mcp_port,
        settings.mcp_streamable_http_path,
        settings.mcp_sse_path,
        len(_registered_tool_names()),
        settings.mcp_tool_profile,
        settings.mcp_expose_advanced_tools,
    )
    mount_path = settings.mcp_mount_path if settings.mcp_transport == 'sse' else None
    mcp.run(transport=settings.mcp_transport, mount_path=mount_path)
