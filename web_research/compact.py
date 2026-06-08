from __future__ import annotations

from typing import Any

from web_research.config import settings


def _truncate(value: Any, limit: int | None = None) -> str:
    text = str(value or '')
    max_chars = limit or settings.mcp_result_excerpt_chars
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - 80)
    return text[:keep].rstrip() + f'\n\n[truncated {len(text) - keep} chars; see saved artifact for full text]'


def _compact_source(source: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: source.get(key)
        for key in (
            'source_id',
            'title',
            'url',
            'final_url',
            'content_type',
            'status_code',
            'rendered',
            'document_metadata',
            'browser_interactions',
        )
        if source.get(key) is not None
    }
    reliability = source.get('reliability')
    if isinstance(reliability, dict):
        compact['reliability'] = {
            key: reliability.get(key)
            for key in ('source_type', 'reliability_weight', 'domain', 'reason')
            if reliability.get(key) is not None
        }
    return compact


def _compact_claim(item: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: item.get(key)
        for key in (
            'claim',
            'claim_id',
            'source_id',
            'support_level',
            'confidence',
            'status',
        )
        if item.get(key) is not None
    }
    for key in ('supporting_sources', 'conflicting_sources', 'citations'):
        if isinstance(item.get(key), list):
            compact[key] = item[key][: settings.mcp_result_max_items]
            compact[f'{key}_count'] = len(item[key])
    return compact


def _compact_query_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return _truncate(item, 500)
    return {
        key: item.get(key)
        for key in ('query', 'intent', 'rationale', 'site', 'decision', 'reason', 'round', 'status', 'gap_code')
        if item.get(key) is not None
    }


def _compact_evidence(item: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: item.get(key)
        for key in ('source_id', 'title', 'url', 'citation', 'rank', 'char_range')
        if item.get(key) is not None
    }
    compact['quote'] = _truncate(item.get('quote') or item.get('text'), min(settings.mcp_result_excerpt_chars, 1200))
    return compact


def _compact_blocked_source(item: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: item.get(key)
        for key in ('url', 'title', 'message', 'blocked', 'block_type', 'block_marker')
        if item.get(key) is not None
    }
    handoff = item.get('manual_handoff')
    if isinstance(handoff, dict):
        compact['manual_handoff'] = {
            key: handoff.get(key)
            for key in ('url', 'message')
            if handoff.get(key) is not None
        }
    return compact


def _compact_link(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ('url', 'text', 'domain', 'file_type')
        if item.get(key) is not None
    }


def _compact_mapping(value: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = {key: value.get(key) for key in keys if value.get(key) is not None}
    return compact or None


def _compact_list(value: Any, *, limit: int | None = None) -> list[Any]:
    if not isinstance(value, list):
        return []
    max_items = limit if limit is not None else settings.mcp_result_max_items
    return value[:max_items]


def _compact_agent_loop(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _compact_mapping(
        value,
        (
            'stop_reason',
            'observed_gaps',
        ),
    ) or {}
    for source_key, count_key in (
        ('planned_queries', 'planned_query_count'),
        ('completed_queries', 'completed_query_count'),
        ('remaining_queries', 'remaining_query_count'),
        ('decisions', 'decision_count'),
        ('rounds', 'round_count'),
    ):
        items = value.get(source_key)
        if isinstance(items, list):
            compact[count_key] = len(items)
            compact[source_key] = [_compact_query_item(item) for item in items[: settings.mcp_result_max_items]]
    return compact or None


def _compact_research_coverage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _compact_mapping(
        value,
        (
            'planned_intent_count',
            'satisfied_intent_count',
            'missing_intents',
            'low_quality_intents',
            'average_intent_quality_score',
            'gaps',
        ),
    ) or {}
    for key in ('missing_intents', 'low_quality_intents', 'gaps', 'by_intent'):
        if isinstance(value.get(key), list):
            compact[key] = [_compact_query_item(item) for item in value[key][: settings.mcp_result_max_items]]
            compact[f'{key}_count'] = len(value[key])
    return compact or None


def _compact_final_answer_review(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _compact_mapping(
        value,
        (
            'ok',
            'issue_count',
            'critical_count',
            'high_count',
            'medium_count',
            'low_count',
        ),
    ) or {}
    if isinstance(value.get('issues'), list):
        compact['issues'] = value['issues'][: settings.mcp_result_max_items]
    contradiction = value.get('contradiction_review')
    if isinstance(contradiction, dict):
        compact['contradiction_review'] = _compact_mapping(
            contradiction,
            (
                'ok',
                'contradiction_count',
                'unresolved_count',
                'message',
            ),
        )
    return compact or None


def _compact_answer_readiness(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _compact_mapping(value, ('ok', 'label', 'score')) or {}
    for key in ('blockers', 'warnings', 'strengths'):
        if isinstance(value.get(key), list):
            compact[key] = [_compact_query_item(item) for item in value[key][: settings.mcp_result_max_items]]
            compact[f'{key}_count'] = len(value[key])
    return compact or None


def _compact_remediation_plan(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _compact_mapping(value, ('ok', 'gap_count', 'action_count')) or {}
    for key in ('gaps', 'actions'):
        if isinstance(value.get(key), list):
            compact[key] = value[key][: settings.mcp_result_max_items]
    return compact or None


def _compact_source_selection_telemetry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _compact_mapping(
        value,
        (
            'planned_read_count',
            'attempted_read_count',
            'selected_source_count',
            'selected_authority_source_count',
            'planned_authority_source_count',
            'planned_low_value_source_count',
            'planned_policy_skip_count',
            'trace_policy_skip_count',
            'read_failure_count',
            'cache_hit_source_count',
            'repeated_domain_count',
            'query_count_with_telemetry',
        ),
    ) or {}
    if isinstance(value.get('per_query'), list):
        compact['per_query'] = value['per_query'][: settings.mcp_result_max_items]
        compact['per_query_count'] = len(value['per_query'])
    for key in ('decision_counts', 'read_selection_reason_counts'):
        if isinstance(value.get(key), dict):
            compact[key] = value[key]
    return compact or None


def _compact_source_quality(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _compact_mapping(
        value,
        (
            'label',
            'score',
            'unique_domain_count',
            'source_count',
            'authority_source_count',
            'low_value_source_count',
            'duplicate_domain_count',
            'primary_source_count',
        ),
    ) or {}
    for key in ('warnings', 'strengths', 'weaknesses', 'domains'):
        if isinstance(value.get(key), list):
            compact[key] = value[key][: settings.mcp_result_max_items]
            compact[f'{key}_count'] = len(value[key])
    for key in ('source_type_counts', 'domain_counts'):
        if isinstance(value.get(key), dict):
            compact[key] = dict(list(value[key].items())[: settings.mcp_result_max_items])
    return compact or None


def _compact_citation_audit(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _compact_mapping(
        value,
        (
            'ok',
            'issue_count',
            'claim_count',
            'cited_claim_count',
            'uncited_claim_count',
            'unsupported_section_count',
        ),
    ) or {}
    for key in ('issues', 'uncited_claim_ids', 'unsupported_sections'):
        if isinstance(value.get(key), list):
            compact[key] = value[key][: settings.mcp_result_max_items]
            compact[f'{key}_count'] = len(value[key])
    return compact or None


def _compact_strategy(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _compact_mapping(
        value,
        (
            'mode',
            'topic',
            'depth',
            'breadth',
            'read_top_per_query',
            'follow_up_rounds',
            'report_format',
            'parent_run_id',
            'follow_up_query',
        ),
    ) or {}
    for key in ('query_plan', 'auto_follow_up_plan', 'completed_plan_keys'):
        if isinstance(value.get(key), list):
            compact[key] = [_compact_query_item(item) for item in value[key][: settings.mcp_result_max_items]]
            compact[f'{key}_count'] = len(value[key])
    return compact or None


def compact_read_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.mcp_compact_results or not isinstance(payload, dict):
        return payload
    compact = dict(payload)
    text = compact.pop('text', None)
    if isinstance(text, str):
        compact['text_excerpt'] = _truncate(text)
        compact['text_omitted_chars'] = max(0, len(text) - len(compact['text_excerpt']))
        compact['compact_result'] = True
    links = list(compact.get('links', []) or [])
    if links:
        max_items = settings.mcp_result_max_items
        compact['links'] = [_compact_link(link) for link in links[:max_items]]
        compact['link_count'] = len(links)
        compact['links_omitted_count'] = max(0, len(links) - len(compact['links']))
    return compact


def compact_research_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.mcp_compact_results or not isinstance(payload, dict):
        return payload
    max_items = settings.mcp_result_max_items
    sources = list(payload.get('sources', []) or [])
    evidence = list(payload.get('evidence', []) or [])
    claims = list(payload.get('claims', []) or [])
    blocked_sources = list(payload.get('blocked_sources', []) or [])
    manual_visit_links = list(payload.get('manual_visit_links', []) or [])
    final_report = str(payload.get('final_report') or '')
    failures = list(payload.get('failures', []) or [])
    tool_status = 'completed' if payload.get('ok') else 'failed'
    tool_status_message = 'Tool call completed.'
    if payload.get('ok') and (failures or blocked_sources):
        tool_status = 'completed_with_source_warnings'
        tool_status_message = (
            'Tool call completed, but some individual sources were blocked or failed. '
            'Use saved artifacts, remaining sources, or follow-up searches instead of retrying the same blocked URL.'
        )
    compact = {
        'ok': payload.get('ok'),
        'tool_status': tool_status,
        'tool_status_message': tool_status_message,
        'compact_result': True,
        'message': payload.get('message'),
        'query': payload.get('query'),
        'question': payload.get('question'),
        'run_id': payload.get('run_id'),
        'run_path': payload.get('run_path'),
        'final_report_path': payload.get('final_report_path'),
        'persistence': payload.get('persistence'),
        'report_format': payload.get('report_format'),
        'strategy': _compact_strategy(payload.get('strategy')),
        'counts': {
            'sources': len(sources),
            'evidence': len(evidence),
            'claims': len(claims),
            'failures': len(failures),
            'blocked_sources': len(blocked_sources),
        },
        'source_quality': _compact_source_quality(payload.get('source_quality')),
        'source_selection_telemetry': _compact_source_selection_telemetry(payload.get('source_selection_telemetry')),
        'research_quality': payload.get('research_quality'),
        'research_coverage': _compact_research_coverage(payload.get('research_coverage')),
        'source_freshness': payload.get('source_freshness'),
        'agent_loop': _compact_agent_loop(payload.get('agent_loop')),
        'citation_validation': payload.get('citation_validation'),
        'citation_audit': _compact_citation_audit(payload.get('citation_audit')),
        'final_answer_review': _compact_final_answer_review(payload.get('final_answer_review')),
        'answer_readiness': _compact_answer_readiness(payload.get('answer_readiness')),
        'source_policy_audit': payload.get('source_policy_audit'),
        'remediation_plan': _compact_remediation_plan(payload.get('remediation_plan')),
        'report_synthesis': payload.get('report_synthesis'),
        'sources': [_compact_source(source) for source in sources[:max_items]],
        'evidence': [_compact_evidence(item) for item in evidence[:max_items]],
        'claims': [_compact_claim(claim) for claim in claims[:max_items] if isinstance(claim, dict)],
        'uncertainties': list(payload.get('uncertainties', []) or [])[:max_items],
        'recent_changes': list(payload.get('recent_changes', []) or [])[:max_items],
        'recommended_next_searches': list(payload.get('recommended_next_searches', []) or [])[:max_items],
        'failures': list(payload.get('failures', []) or [])[:max_items],
        'blocked_sources': [_compact_blocked_source(item) for item in blocked_sources[:max_items]],
        'manual_visit_links': manual_visit_links[:max_items],
        'final_report_excerpt': _truncate(final_report),
        'artifact_note': 'Full run JSON and report are saved on disk; use run_path/final_report_path for the complete dossier.',
    }
    return {key: value for key, value in compact.items() if value is not None}
