from __future__ import annotations

from typing import Any


def _plan_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, 'to_dict'):
        return item.to_dict()
    return {}


PRIMARY_INTENTS = {
    'primary_source',
    'government_source',
    'federal_register',
    'known_official_site',
    'policy_guidance',
    'company_source',
    'government_data',
    'academic_source',
}

DOCUMENTATION_INTENTS = {'documentation', 'repository'}
FRESHNESS_INTENTS = {'freshness'}
COUNTERPOINT_INTENTS = {'counterpoint'}
CONTRADICTION_INTENTS = {'contradiction_resolution'}


def _status_for_intent(completed_count: int, selected_source_count: int, matched_source_count: int) -> str:
    if matched_source_count > 0:
        return 'satisfied'
    if selected_source_count > 0:
        return 'selected_unmatched'
    if completed_count > 0:
        return 'attempted_no_sources'
    return 'missing'


def _intent_match_required(intent: str) -> bool:
    return intent in PRIMARY_INTENTS | DOCUMENTATION_INTENTS | FRESHNESS_INTENTS | COUNTERPOINT_INTENTS | CONTRADICTION_INTENTS


def _trace_matches_intent(trace: dict[str, Any], intent: str) -> bool:
    if not _intent_match_required(intent):
        return True
    source_type = str(trace.get('source_type') or '').lower()
    weight = str(trace.get('reliability_weight') or '').lower()
    haystack = ' '.join(
        str(trace.get(key) or '')
        for key in ('title', 'url', 'final_url', 'recovered_url', 'message')
    ).lower()
    reasons = {str(item) for item in trace.get('source_score_reasons', []) or []}

    if intent in PRIMARY_INTENTS:
        return (
            weight == 'strong'
            or source_type in {'government', 'academic', 'documentation', 'repository'}
            or 'primary_tld' in reasons
            or 'primary_source_hint' in reasons
            or any(marker in haystack for marker in ('official', '.gov/', '.edu/', 'docs.', '/docs', 'federalregister.gov'))
        )
    if intent in DOCUMENTATION_INTENTS:
        return (
            source_type in {'documentation', 'repository'}
            or 'documentation_or_repository' in reasons
            or any(marker in haystack for marker in ('docs.', '/docs', 'documentation', 'github.com'))
        )
    if intent in FRESHNESS_INTENTS:
        return any(marker in haystack for marker in ('latest', 'release', 'changelog', 'updated', 'news', 'blog'))
    if intent in COUNTERPOINT_INTENTS | CONTRADICTION_INTENTS:
        if any(marker in haystack for marker in ('limitation', 'limits', 'criticism', 'issue', 'problem', 'risk', 'caveat', 'conflict', 'correction', 'clarification')):
            return True
        if intent in CONTRADICTION_INTENTS:
            return weight in {'strong', 'medium'} or source_type in {'government', 'academic', 'documentation', 'repository', 'news'}
        return False
    return True


def _source_quality_points(trace: dict[str, Any], *, matched: bool) -> tuple[int, list[str]]:
    source_type = str(trace.get('source_type') or '').lower()
    weight = str(trace.get('reliability_weight') or '').lower()
    reasons = [str(item) for item in trace.get('source_score_reasons', []) or []]
    intent_reasons = [str(item) for item in trace.get('source_intent_reasons', []) or []]
    points = 12
    notes: list[str] = []

    if matched:
        points += 18
        notes.append('matched_intent')
    else:
        points -= 8
        notes.append('unmatched_intent')
    if weight == 'strong':
        points += 22
        notes.append('strong_source')
    elif weight == 'medium':
        points += 12
        notes.append('medium_source')
    elif weight == 'supporting':
        points += 4
        notes.append('supporting_source')
    if source_type in {'government', 'academic', 'documentation', 'repository'}:
        points += 16
        notes.append(f'{source_type}_source')
    elif source_type == 'news':
        points += 8
        notes.append('news_source')
    elif source_type in {'forum', 'blog'}:
        points -= 4
        notes.append(f'{source_type}_source')
    if 'primary_source_hint' in reasons or 'primary_tld' in reasons:
        points += 8
        notes.append('primary_signal')
    if 'documentation_or_repository' in reasons:
        points += 6
        notes.append('docs_or_repository_signal')
    if intent_reasons:
        points += min(12, len(intent_reasons) * 4)
        notes.extend(intent_reasons[:3])
    try:
        intent_score = int(trace.get('source_intent_score') or 0)
    except (TypeError, ValueError):
        intent_score = 0
    if intent_score:
        points += min(10, max(0, intent_score // 5))
        notes.append('intent_ranked_source')
    return max(0, min(100, points)), notes


def _quality_label(score: int) -> str:
    if score >= 75:
        return 'strong'
    if score >= 50:
        return 'adequate'
    if score >= 25:
        return 'thin'
    return 'weak'


def _intent_quality_score(
    *,
    selected_for_intent: list[dict[str, Any]],
    matched_flags: list[bool],
    selected_count: int,
    matched_count: int,
    failed_count: int,
) -> dict[str, Any]:
    source_scores = []
    source_notes: list[str] = []
    for trace, matched in zip(selected_for_intent, matched_flags):
        score, notes = _source_quality_points(trace, matched=matched)
        source_scores.append(score)
        for note in notes:
            if note not in source_notes:
                source_notes.append(note)
    domains = {
        str(item.get('final_url') or item.get('url') or '').split('/')[2].lower()
        for item in selected_for_intent
        if '//' in str(item.get('final_url') or item.get('url') or '')
    }
    if matched_count:
        base = 30
    elif selected_count:
        base = 15
    else:
        base = 0
    if source_scores:
        score = round((base + max(source_scores) + (sum(source_scores) / len(source_scores))) / 3)
    else:
        score = base
    if matched_count and max(source_scores, default=0) >= 75:
        score += 5
        source_notes.append('high_quality_matched_source')
    if matched_count >= 2:
        score += 8
        source_notes.append('multi_source_intent_support')
    if len(domains) >= 2:
        score += 5
        source_notes.append('domain_diversity')
    if failed_count:
        score -= min(20, failed_count * 6)
        source_notes.append('failed_or_blocked_sources')
    score = max(0, min(100, int(score)))
    return {
        'score': score,
        'label': _quality_label(score),
        'best_source_score': max(source_scores, default=0),
        'average_source_score': round(sum(source_scores) / len(source_scores), 1) if source_scores else 0,
        'unique_domain_count': len(domains),
        'quality_signals': source_notes[:8],
    }


def build_research_coverage(
    *,
    query_plan: list[Any] | None = None,
    searches: list[dict[str, Any]] | None = None,
    selection_trace: list[dict[str, Any]] | None = None,
    source_quality: dict[str, Any] | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    plans = [_plan_dict(item) for item in query_plan or []]
    if not plans and query:
        plans = [{'query': query, 'intent': 'single_search', 'rationale': 'Single research_web query.'}]

    searches = searches or []
    selection_trace = selection_trace or []
    source_quality = source_quality or {}
    planned_intents = []
    for item in plans:
        intent = str(item.get('intent') or 'unknown')
        if intent not in planned_intents:
            planned_intents.append(intent)
    for item in searches:
        intent = str(item.get('intent') or 'single_search')
        if intent not in planned_intents:
            planned_intents.append(intent)

    selected_trace = [
        item
        for item in selection_trace
        if item.get('decision') in {'selected', 'selected_recovery'} and item.get('source_id') is not None
    ]
    failed_trace = [
        item
        for item in selection_trace
        if item.get('decision') in {'read_failed', 'skipped_blocked_domain'}
    ]

    by_intent = []
    missing_intents = []
    for intent in planned_intents:
        planned_queries = [item for item in plans if str(item.get('intent') or 'unknown') == intent]
        completed_searches = [item for item in searches if str(item.get('intent') or 'single_search') == intent]
        selected_for_intent = [item for item in selected_trace if str(item.get('intent') or 'single_search') == intent]
        selected_count = len(selected_for_intent)
        matched_flags = [_trace_matches_intent(item, intent) for item in selected_for_intent]
        matched_count = sum(1 for item in matched_flags if item)
        failed_count = sum(1 for item in failed_trace if str(item.get('intent') or 'single_search') == intent)
        status = _status_for_intent(len(completed_searches), selected_count, matched_count)
        quality = _intent_quality_score(
            selected_for_intent=selected_for_intent,
            matched_flags=matched_flags,
            selected_count=selected_count,
            matched_count=matched_count,
            failed_count=failed_count,
        )
        if status != 'satisfied':
            missing_intents.append(intent)
        by_intent.append(
            {
                'intent': intent,
                'status': status,
                'quality_score': quality['score'],
                'quality_label': quality['label'],
                'best_source_score': quality['best_source_score'],
                'average_source_score': quality['average_source_score'],
                'unique_domain_count': quality['unique_domain_count'],
                'quality_signals': quality['quality_signals'],
                'planned_query_count': len(planned_queries),
                'completed_query_count': len(completed_searches),
                'selected_source_count': selected_count,
                'matched_source_count': matched_count,
                'selected_unmatched_count': max(0, selected_count - matched_count),
                'failed_or_blocked_count': failed_count,
                'queries': [
                    {
                        'query': item.get('query'),
                        'site': item.get('site'),
                        'rationale': item.get('rationale'),
                    }
                    for item in planned_queries[:3]
                ],
            }
        )

    gaps = []
    if missing_intents:
        gaps.append(f'Missing or unsatisfied plan intents: {", ".join(missing_intents)}.')
    if int(source_quality.get('primary_source_count') or 0) == 0 and int(source_quality.get('selected_source_count') or 0) > 0:
        gaps.append('No strong primary source was selected.')
    if int(source_quality.get('unique_domain_count') or 0) < 2 and int(source_quality.get('selected_source_count') or 0) > 0:
        gaps.append('Selected sources do not yet span multiple domains.')

    satisfied_count = sum(1 for item in by_intent if item['status'] == 'satisfied')
    attempted_count = sum(1 for item in by_intent if item['status'] != 'missing')
    intent_scores = [int(item.get('quality_score') or 0) for item in by_intent if item['status'] != 'missing']
    average_intent_quality = round(sum(intent_scores) / len(intent_scores), 1) if intent_scores else 0
    low_quality_intents = [item['intent'] for item in by_intent if item['status'] != 'missing' and int(item.get('quality_score') or 0) < 50]
    if low_quality_intents:
        gaps.append(f'Low-quality source coverage for intent(s): {", ".join(low_quality_intents)}.')
    return {
        'planned_intent_count': len(planned_intents),
        'satisfied_intent_count': satisfied_count,
        'attempted_intent_count': attempted_count,
        'average_intent_quality_score': average_intent_quality,
        'low_quality_intents': low_quality_intents,
        'missing_intents': missing_intents,
        'by_intent': by_intent,
        'gaps': gaps,
    }
