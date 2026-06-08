from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from web_research.claims import extract_claims_from_evidence, recent_change_notes, uncertainty_notes
from web_research.claim_support import build_claim_support_table
from web_research.citations import audit_citations
from web_research.coverage import build_research_coverage
from web_research.credibility import credibility_assessment
from web_research.evidence_index import build_evidence_index
from web_research.fetch import read_url
from web_research.freshness import build_freshness_summary
from web_research.local_llm import review_claim_contradictions
from web_research.planner import plan_source_reads, rerank_search_results
from web_research.recovery import build_recovery_candidates
from web_research.report import assess_research_quality, finalize_report_payload, normalize_report_format, recommended_next_searches, validate_citations
from web_research.runs import save_research_run
from web_research.search import normalize_url, web_search
from web_research.source_policy import research_skip_reason, should_attempt_recovery


def _manual_handoff(url: str) -> dict[str, str]:
    return {
        'url': url,
        'message': 'Open this page manually if you are authorized to access it. Complete any required site check in your browser, then retry with BROWSER_PROFILE_DIR set to that browser profile.',
    }


def _same_domain(url: str, domain: str) -> bool:
    return (urlparse(url).hostname or '').lower() == domain


TRACKING_QUERY_PREFIXES = ('utm_',)
TRACKING_QUERY_KEYS = {'fbclid', 'gclid', 'mc_cid', 'mc_eid', 'ref', 'ref_src'}


def _canonical_source_key(url: str) -> str:
    parsed = urlparse(normalize_url(url))
    if parsed.scheme not in {'http', 'https'}:
        return normalize_url(url)
    host = (parsed.hostname or '').lower().removeprefix('www.')
    netloc = f'{host}:{parsed.port}' if parsed.port else host
    path = parsed.path.rstrip('/') or '/'
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    query = urlencode(sorted(query_items))
    return urlunparse((parsed.scheme.lower(), netloc, path, '', query, ''))


def _source_key_from_payload(source: dict[str, Any]) -> str:
    return _canonical_source_key(str(source.get('final_url') or source.get('url') or ''))


def _selection_metadata(result: dict[str, Any]) -> dict[str, Any]:
    return {
        'source_score': result.get('source_score'),
        'source_score_reasons': result.get('source_score_reasons', []),
        'original_rank': result.get('original_rank'),
        'source_intent': result.get('source_intent'),
        'source_intent_score': result.get('source_intent_score'),
        'source_intent_reasons': result.get('source_intent_reasons', []),
        'read_selection_reason': result.get('read_selection_reason'),
        'read_selection_rank': result.get('read_selection_rank'),
    }


AUTHORITY_SCORE_REASONS = {
    'primary_tld',
    'primary_source_hint',
    'documentation_or_repository',
    'market_authority_domain',
    'market_authority_hint',
    'market_primary_data_source',
}

LOW_VALUE_SCORE_REASONS = {
    'low_value_hint',
    'market_low_value_hint',
    'market_low_value_domain',
    'secondary_or_social_domain',
    'academic_social_or_gate_domain',
    'discussion_domain_without_counterpoint_signal',
}


def _count_values(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or '').strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def build_source_selection_telemetry(
    planned_reads: list[dict[str, Any]],
    selection_trace: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    planned_domains: dict[str, int] = {}
    for item in planned_reads:
        domain = (urlparse(str(item.get('url') or '')).hostname or '').lower().removeprefix('www.')
        if domain:
            planned_domains[domain] = planned_domains.get(domain, 0) + 1
    repeated_domains = {domain: count for domain, count in planned_domains.items() if count > 1}

    planned_policy_skips = [
        item
        for item in planned_reads
        if item.get('source_policy_skip_reason')
        or any(str(reason).startswith('source_policy_skip:') for reason in item.get('source_score_reasons', []) or [])
    ]
    planned_authority = [
        item
        for item in planned_reads
        if AUTHORITY_SCORE_REASONS & {str(reason) for reason in item.get('source_score_reasons', []) or []}
    ]
    planned_low_value = [
        item
        for item in planned_reads
        if LOW_VALUE_SCORE_REASONS & {str(reason) for reason in item.get('source_score_reasons', []) or []}
    ]
    selected_trace = [item for item in selection_trace if item.get('decision') in {'selected', 'selected_recovery'}]
    selected_authority = [
        item
        for item in selected_trace
        if AUTHORITY_SCORE_REASONS & {str(reason) for reason in item.get('source_score_reasons', []) or []}
    ]

    return {
        'planned_read_count': len(planned_reads),
        'attempted_read_count': len(selection_trace),
        'selected_source_count': len(selected_trace),
        'planned_authority_source_count': len(planned_authority),
        'selected_authority_source_count': len(selected_authority),
        'planned_low_value_source_count': len(planned_low_value),
        'planned_policy_skip_count': len(planned_policy_skips),
        'trace_policy_skip_count': sum(1 for item in selection_trace if item.get('decision') == 'skipped_source_policy'),
        'duplicate_skip_count': sum(
            1 for item in selection_trace if str(item.get('decision') or '').startswith('skipped_duplicate')
        ),
        'read_failure_count': sum(1 for item in selection_trace if item.get('decision') == 'read_failed'),
        'recovery_selected_count': sum(1 for item in selection_trace if item.get('decision') == 'selected_recovery'),
        'cache_hit_source_count': sum(1 for source in sources if isinstance(source, dict) and source.get('cached')),
        'repeated_domain_count': len(repeated_domains),
        'repeated_domains': dict(sorted(repeated_domains.items(), key=lambda pair: (-pair[1], pair[0]))[:10]),
        'decision_counts': _count_values(selection_trace, 'decision'),
        'read_selection_reason_counts': _count_values(planned_reads, 'read_selection_reason'),
        'top_source_score_reasons': [
            {'reason': reason, 'count': count}
            for reason, count in sorted(
                {
                    str(reason): sum(
                        1
                        for item in planned_reads
                        if str(reason) in {str(value) for value in item.get('source_score_reasons', []) or []}
                    )
                    for read in planned_reads
                    for reason in read.get('source_score_reasons', []) or []
                }.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:10]
        ],
    }


def _source_type(domain: str, url: str) -> str:
    lowered_url = url.lower()
    if domain.endswith('.gov'):
        return 'government'
    if domain.endswith('.edu'):
        return 'academic'
    if 'github.com' in domain or domain == 'raw.githubusercontent.com':
        return 'repository'
    if 'docs.' in domain or '/docs' in lowered_url or 'documentation' in lowered_url:
        return 'documentation'
    if any(host in domain for host in ('reddit.com', 'news.ycombinator.com', 'stackoverflow.com')):
        return 'forum'
    if any(host in domain for host in ('medium.com', 'dev.to', 'substack.com')):
        return 'blog'
    if any(term in domain for term in ('news', 'times', 'post', 'reuters', 'apnews', 'bbc', 'cnn')):
        return 'news'
    return 'web'


def _source_reliability_notes(source: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    url = str(source.get('final_url') or source.get('url') or result.get('url') or '')
    domain = (urlparse(url).hostname or '').lower().removeprefix('www.')
    source_type = _source_type(domain, url)
    credibility = credibility_assessment(url, source_type=source_type)
    score = int(result.get('source_score') or 0)
    reasons = [str(item) for item in result.get('source_score_reasons', []) or []]
    why_selected = []
    why_trusted = []
    caveats = []

    if score:
        why_selected.append(f'Ranked with source score {score}.')
    if reasons:
        why_selected.append(f"Ranking signals: {', '.join(reasons[:4])}.")
    if source_type in {'government', 'academic', 'documentation', 'repository'}:
        why_trusted.append(f'Source type is {source_type}, usually stronger for primary evidence.')
    elif source_type == 'news':
        why_trusted.append('News source can help with recent context, but should be checked against primary sources.')
    elif source_type in {'blog', 'forum'}:
        caveats.append(f'Source type is {source_type}; treat as supporting or experiential evidence unless independently confirmed.')
    if source.get('recovered_from'):
        caveats.append('Content was read from a same-domain recovery URL rather than the original search result URL.')
    if not source.get('fetched_at'):
        caveats.append('No fetched_at timestamp was recorded for this source.')
    if not why_selected:
        why_selected.append('Selected from ranked search results.')
    if not why_trusted and not caveats:
        why_trusted.append('Readable source with extractable evidence.')

    if source_type in {'government', 'academic', 'documentation', 'repository'}:
        weight = 'strong'
    elif source_type == 'news':
        weight = 'medium'
    elif source_type in {'blog', 'forum'}:
        weight = 'supporting'
    else:
        weight = 'medium' if score >= 100 else 'supporting'

    return {
        'source_type': source_type,
        'reliability_weight': weight,
        'credibility': credibility,
        'why_selected': why_selected,
        'why_trusted': why_trusted + [str(item) for item in credibility.get('reasons', [])],
        'caveats': caveats + [str(item) for item in credibility.get('caveats', [])],
    }


def _source_quality_summary(sources: list[dict[str, Any]], selection_trace: list[dict[str, Any]]) -> dict[str, Any]:
    selected_trace = [item for item in selection_trace if item.get('decision') in {'selected', 'selected_recovery'}]
    scores = [item.get('source_score') for item in selected_trace if isinstance(item.get('source_score'), int)]
    reason_counts: dict[str, int] = {}
    for item in selected_trace:
        for reason in item.get('source_score_reasons', []) or []:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
    domains = sorted(
        {
            (urlparse(str(source.get('final_url') or source.get('url') or '')).hostname or '').lower()
            for source in sources
            if source.get('final_url') or source.get('url')
        }
    )
    domains = [domain for domain in domains if domain]
    source_type_counts: dict[str, int] = {}
    reliability_weight_counts: dict[str, int] = {}
    credibility_label_counts: dict[str, int] = {}
    credibility_scores: list[int] = []
    for source in sources:
        reliability = source.get('reliability') if isinstance(source.get('reliability'), dict) else {}
        source_type = str(reliability.get('source_type') or 'unknown')
        reliability_weight = str(reliability.get('reliability_weight') or 'unknown')
        credibility = reliability.get('credibility') if isinstance(reliability.get('credibility'), dict) else {}
        credibility_label = str(credibility.get('label') or 'unknown')
        source_type_counts[source_type] = source_type_counts.get(source_type, 0) + 1
        reliability_weight_counts[reliability_weight] = reliability_weight_counts.get(reliability_weight, 0) + 1
        credibility_label_counts[credibility_label] = credibility_label_counts.get(credibility_label, 0) + 1
        if isinstance(credibility.get('score'), int):
            credibility_scores.append(int(credibility['score']))
    downgrade_reasons = []
    if not sources:
        downgrade_reasons.append(
            {'reason': 'no_readable_sources', 'message': 'No readable sources were selected.', 'severity': 'high'}
        )
    elif len(domains) < 2:
        downgrade_reasons.append(
            {
                'reason': 'low_domain_diversity',
                'message': 'Selected sources do not yet span multiple domains.',
                'severity': 'medium',
            }
        )
    if len(sources) >= 3 and len(domains) < 3:
        downgrade_reasons.append(
            {
                'reason': 'thin_domain_diversity',
                'message': 'Three or more sources were selected, but they cover fewer than three domains.',
                'severity': 'medium',
            }
        )
    duplicate_count = sum(1 for item in selection_trace if item.get('decision') == 'skipped_duplicate_url')
    if duplicate_count:
        downgrade_reasons.append(
            {
                'reason': 'duplicate_heavy_results',
                'message': f'{duplicate_count} duplicate search result(s) were skipped before source selection.',
                'severity': 'low',
                'count': duplicate_count,
            }
        )
    if sources and reliability_weight_counts.get('strong', 0) == 0:
        downgrade_reasons.append(
            {
                'reason': 'no_strong_primary_sources',
                'message': 'No strong primary sources were selected.',
                'severity': 'high',
            }
        )
    if sources and not any(label in credibility_label_counts for label in ('high', 'medium')):
        downgrade_reasons.append(
            {
                'reason': 'low_credibility_source_set',
                'message': 'No selected source has a high or medium credibility signal.',
                'severity': 'high',
            }
        )
    supporting_count = reliability_weight_counts.get('supporting', 0)
    if sources and supporting_count >= max(2, len(sources) // 2 + 1):
        downgrade_reasons.append(
            {
                'reason': 'supporting_source_heavy',
                'message': 'Most selected sources are supporting rather than primary or medium-weight sources.',
                'severity': 'medium',
            }
        )
    return {
        'selected_source_count': len(sources),
        'unique_domain_count': len(domains),
        'domains': domains,
        'source_type_counts': source_type_counts,
        'reliability_weight_counts': reliability_weight_counts,
        'credibility_label_counts': credibility_label_counts,
        'average_credibility_score': round(sum(credibility_scores) / len(credibility_scores), 1) if credibility_scores else None,
        'primary_source_count': reliability_weight_counts.get('strong', 0),
        'supporting_source_count': reliability_weight_counts.get('supporting', 0),
        'average_source_score': round(sum(scores) / len(scores), 1) if scores else None,
        'rendered_source_count': sum(1 for source in sources if source.get('rendered')),
        'canonicalized_source_count': sum(1 for source in sources if source.get('access_strategy') == 'canonicalized'),
        'downgrade_reasons': downgrade_reasons,
        'top_score_reasons': [
            {'reason': reason, 'count': count}
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        ],
        'strongest_sources': [
            {
                'source_id': source.get('source_id'),
                'title': source.get('title'),
                'url': source.get('final_url') or source.get('url'),
                'source_type': source.get('reliability', {}).get('source_type'),
                'reliability_weight': source.get('reliability', {}).get('reliability_weight'),
                'credibility': source.get('reliability', {}).get('credibility'),
            }
            for source in sources
            if source.get('reliability', {}).get('reliability_weight') == 'strong'
        ][:5],
        'weaker_supporting_sources': [
            {
                'source_id': source.get('source_id'),
                'title': source.get('title'),
                'url': source.get('final_url') or source.get('url'),
                'source_type': source.get('reliability', {}).get('source_type'),
                'reliability_weight': source.get('reliability', {}).get('reliability_weight'),
                'credibility': source.get('reliability', {}).get('credibility'),
            }
            for source in sources
            if source.get('reliability', {}).get('reliability_weight') == 'supporting'
        ][:5],
    }


async def research_web(
    query: str,
    max_results: int = 8,
    read_top: int = 4,
    freshness: str | None = None,
    site: str | None = None,
    render: bool = False,
    report_format: str = 'long_report',
    persist: bool = True,
    source_intent: str | None = None,
) -> dict[str, Any]:
    max_results = max(1, min(max_results, 20))
    read_top = max(1, min(read_top, max_results, 3))
    report_format = normalize_report_format(report_format)
    search_payload = web_search(query=query, max_results=max_results, freshness=freshness, site=site)
    ranked_results = rerank_search_results(search_payload.get('results', []), query=query)
    search_payload = dict(search_payload, results=ranked_results)
    results = search_payload.get('results', [])
    max_inspected = max(read_top + 2, read_top * 3)
    planned_reads = plan_source_reads(results, read_top=read_top, inspect_limit=max_inspected, source_intent=source_intent)
    sources: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    selection_trace: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_source_keys: set[str] = set()
    blocked_domains: set[str] = set()
    source_id = 1
    inspected = 0
    for result in planned_reads:
        if len(sources) >= read_top:
            break
        if inspected >= max_inspected:
            break
        inspected += 1
        url = normalize_url(result['url'])
        domain = (urlparse(url).hostname or '').lower()
        source_key = _canonical_source_key(url)
        skip_reason = research_skip_reason(url)
        if skip_reason:
            failure = {
                'url': url,
                'title': result.get('title'),
                'message': f'skipped by source policy: {skip_reason}',
                'skipped': True,
                'skip_reason': skip_reason,
            }
            failures.append(failure)
            selection_trace.append(
                {
                    'url': url,
                    'title': result.get('title'),
                    'decision': 'skipped_source_policy',
                    'skip_reason': skip_reason,
                    **_selection_metadata(result),
                }
            )
            continue
        if source_key in seen:
            selection_trace.append(
                {
                    'url': url,
                    'title': result.get('title'),
                    'decision': 'skipped_duplicate_url',
                    'duplicate_key': source_key,
                    **_selection_metadata(result),
                }
            )
            continue
        if domain in blocked_domains:
            skipped_failure = {
                'url': url,
                'title': result.get('title'),
                'message': f'skipped after repeated blocking from {domain}',
                'blocked': True,
                'block_type': 'blocked',
                'manual_handoff': _manual_handoff(url),
            }
            failures.append(skipped_failure)
            selection_trace.append(
                {
                    'url': url,
                    'title': result.get('title'),
                    'decision': 'skipped_blocked_domain',
                    'message': skipped_failure['message'],
                    **_selection_metadata(result),
                }
            )
            continue
        seen.add(source_key)
        payload = await read_url(url, query=query, render=render, source_id=source_id)
        if (
            not payload.get('ok')
            and not render
            and not payload.get('blocked')
            and payload.get('message') == 'URL fetched'
            and not payload.get('text')
        ):
            payload = await read_url(url, query=query, render=True, source_id=source_id)
        if payload.get('ok'):
            source = dict(payload)
            source['search_result'] = result
            source['reliability'] = _source_reliability_notes(source, result)
            resolved_key = _source_key_from_payload(source)
            if resolved_key in seen_source_keys:
                selection_trace.append(
                    {
                        'url': url,
                        'title': result.get('title'),
                        'decision': 'skipped_duplicate_resolved_url',
                        'duplicate_key': resolved_key,
                        **_selection_metadata(result),
                    }
                )
                continue
            seen_source_keys.add(resolved_key)
            sources.append(source)
            reliability = source.get('reliability') if isinstance(source.get('reliability'), dict) else {}
            selection_trace.append(
                {
                    'url': url,
                    'final_url': source.get('final_url'),
                    'title': result.get('title'),
                    'decision': 'selected',
                    'source_id': source_id,
                    'rendered': payload.get('rendered', False),
                    'source_type': reliability.get('source_type'),
                    'reliability_weight': reliability.get('reliability_weight'),
                    **_selection_metadata(result),
                }
            )
            source_id += 1
        else:
            message = payload.get('message', 'read failed')
            lowered = message.lower()
            if domain and (
                payload.get('blocked')
                or any(marker in lowered for marker in ('blocked', 'captcha', 'challenge', 'forbidden', 'enable javascript'))
            ):
                blocked_domains.add(domain)
            failure = {'url': url, 'title': result.get('title'), 'message': message}
            trace_item = {
                'url': url,
                'title': result.get('title'),
                'decision': 'read_failed',
                'message': message,
                **_selection_metadata(result),
            }
            if payload.get('blocked'):
                failure.update(
                    {
                        'blocked': True,
                        'block_type': payload.get('block_type', 'blocked'),
                        'block_marker': payload.get('block_marker'),
                        'manual_handoff': _manual_handoff(url),
                        'recovery_attempts': [],
                    }
                )
                can_recover = should_attempt_recovery(
                    url,
                    block_marker=str(payload.get('block_marker') or ''),
                    message=str(payload.get('message') or ''),
                )
                if not can_recover:
                    failure['recovery_skipped'] = True
                    failure['recovery_skip_reason'] = 'hard_block_or_no_recovery_domain'
                    trace_item['recovery_skipped'] = True
                    trace_item['recovery_skip_reason'] = 'hard_block_or_no_recovery_domain'
                for candidate in build_recovery_candidates(url, limit=1) if can_recover else []:
                    candidate_url = normalize_url(candidate.url)
                    candidate_key = _canonical_source_key(candidate_url)
                    if candidate_key in seen or not _same_domain(candidate_url, domain):
                        continue
                    seen.add(candidate_key)
                    recovery_payload = await read_url(candidate_url, query=query, render=render, source_id=source_id)
                    attempt = {
                        'url': candidate_url,
                        'strategy': candidate.strategy,
                        'reason': candidate.reason,
                        'ok': bool(recovery_payload.get('ok')),
                    }
                    if recovery_payload.get('blocked'):
                        attempt['blocked'] = True
                        attempt['block_type'] = recovery_payload.get('block_type', 'blocked')
                    elif not recovery_payload.get('ok'):
                        attempt['message'] = recovery_payload.get('message', 'read failed')
                    failure['recovery_attempts'].append(attempt)
                    if recovery_payload.get('ok'):
                        source = dict(recovery_payload)
                        source['search_result'] = result
                        source['recovered_from'] = {
                            'url': url,
                            'strategy': candidate.strategy,
                            'reason': candidate.reason,
                        }
                        source['reliability'] = _source_reliability_notes(source, result)
                        resolved_key = _source_key_from_payload(source)
                        if resolved_key in seen_source_keys:
                            trace_item.update(
                                {
                                    'decision': 'skipped_duplicate_resolved_url',
                                    'duplicate_key': resolved_key,
                                    'recovered_url': candidate_url,
                                }
                            )
                            break
                        seen_source_keys.add(resolved_key)
                        sources.append(source)
                        reliability = source.get('reliability') if isinstance(source.get('reliability'), dict) else {}
                        trace_item.update(
                            {
                                'decision': 'selected_recovery',
                                'source_id': source_id,
                                'recovered_url': candidate_url,
                                'rendered': recovery_payload.get('rendered', False),
                                'source_type': reliability.get('source_type'),
                                'reliability_weight': reliability.get('reliability_weight'),
                                **_selection_metadata(result),
                            }
                        )
                        source_id += 1
                        break
            failures.append(failure)
            selection_trace.append(trace_item)
    traced_urls = {str(item.get('url') or '') for item in selection_trace if isinstance(item, dict)}
    for result in planned_reads:
        url = normalize_url(result['url'])
        if url in traced_urls:
            continue
        skip_reason = research_skip_reason(url)
        if not skip_reason:
            continue
        selection_trace.append(
            {
                'url': url,
                'title': result.get('title'),
                'decision': 'skipped_source_policy',
                'deferred': True,
                'skip_reason': skip_reason,
                **_selection_metadata(result),
            }
        )
        traced_urls.add(url)
    evidence = []
    for source in sources:
        evidence.extend(source.get('evidence', []))
    evidence.sort(key=lambda item: (item.get('rank', 999), item.get('source_id', 999)))
    evidence_index = build_evidence_index(query, sources, evidence)
    claims = extract_claims_from_evidence(evidence)
    claim_support = build_claim_support_table(claims, evidence_index)
    claim_review = await review_claim_contradictions(claims)
    blocked_sources = [failure for failure in failures if failure.get('blocked')]
    source_quality = _source_quality_summary(sources, selection_trace)
    source_selection_telemetry = build_source_selection_telemetry(planned_reads, selection_trace, sources)
    payload = {
        'ok': bool(sources),
        'query': query,
        'freshness': freshness,
        'site': site,
        'render': render,
        'report_format': report_format,
        'source_intent': source_intent,
        'search': search_payload,
        'planned_reads': planned_reads,
        'sources': sources,
        'evidence': evidence,
        'evidence_index': evidence_index,
        'citations': [item['citation'] for item in evidence],
        'claims': claims,
        'claim_support': claim_support,
        'claim_review': claim_review,
        'uncertainties': uncertainty_notes(claims=claims, failures=failures, blocked_sources=blocked_sources),
        'recent_changes': recent_change_notes(evidence),
        'source_quality': source_quality,
        'source_selection_telemetry': source_selection_telemetry,
        'research_coverage': build_research_coverage(
            searches=[
                {
                    'query': query,
                    'intent': 'single_search',
                    'source_intent': source_intent,
                    'ok': bool(sources),
                    'source_count': len(sources),
                }
            ],
            selection_trace=selection_trace,
            source_quality=source_quality,
            query=query,
        ),
        'selection_trace': selection_trace,
        'failures': failures,
        'blocked_sources': blocked_sources,
        'manual_visit_links': [
            failure['manual_handoff']
            for failure in failures
            if failure.get('blocked') and failure.get('manual_handoff')
        ],
        'message': 'Research completed with sources' if sources else 'Search completed but no sources could be read',
    }
    payload['source_freshness'] = build_freshness_summary(payload)
    payload['citation_validation'] = validate_citations(payload)
    payload['citation_audit'] = audit_citations(payload)
    payload['research_quality'] = assess_research_quality(payload)
    payload['recommended_next_searches'] = recommended_next_searches(payload)
    await finalize_report_payload(payload, report_format=report_format)
    if persist:
        try:
            persistence = await asyncio.to_thread(save_research_run, 'research_web', query, payload)
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
    else:
        payload['persistence'] = {'saved': False, 'message': 'Persistence disabled for child research run.'}
    return payload
