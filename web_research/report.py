from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from web_research.claim_support import build_claim_support_table
from web_research.citations import audit_citations, validate_citations
from web_research.remediation import build_research_remediation_plan
from web_research.review import adversarial_final_answer_review, contradiction_review

REPORT_FORMATS = ('quick_answer', 'source_table', 'executive_brief', 'long_report', 'comparison_matrix')


def normalize_report_format(report_format: str | None) -> str:
    if report_format in REPORT_FORMATS:
        return str(report_format)
    return 'long_report'


def _line_items(values: list[str], *, empty: str) -> list[str]:
    if not values:
        return [f'- {empty}']
    return [f'- {value}' for value in values]


def recommended_next_searches(payload: dict[str, Any], *, limit: int = 5) -> list[str]:
    question = str(payload.get('question') or payload.get('query') or '').strip()
    uncertainties = payload.get('uncertainties', []) or []
    research_quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
    source_freshness = payload.get('source_freshness') if isinstance(payload.get('source_freshness'), dict) else {}
    claims = payload.get('claims', []) or []
    contradictions = contradiction_review(claims, question=question)
    searches = []
    remediation = payload.get('remediation_plan') if isinstance(payload.get('remediation_plan'), dict) else build_research_remediation_plan(payload)
    for action in remediation.get('actions', []) or []:
        if isinstance(action, dict) and action.get('query'):
            searches.append(str(action['query']))
    if question:
        searches.append(f'{question} official source')
        searches.append(f'{question} latest update')
        if int(source_quality.get('primary_source_count') or 0) == 0:
            searches.append(f'{question} primary source documentation official')
        if int(source_quality.get('unique_domain_count') or 0) < 3:
            searches.append(f'{question} independent sources')
        if int(research_quality.get('blocked_source_count') or 0) > 0:
            searches.append(f'{question} alternative accessible source')
        if int(research_quality.get('conflicted_claim_count') or 0) > 0:
            searches.append(f'{question} contradiction verification')
        searches.extend(contradictions.get('follow_up_searches', []) or [])
        if source_freshness.get('current_sensitive') and not source_freshness.get('content_freshness_evidence'):
            searches.append(f'{question} changelog release notes latest')
    for note in uncertainties[:3]:
        if question:
            searches.append(f'{question} {str(note)[:80]}')
    seen = []
    for search in searches:
        if search not in seen:
            seen.append(search)
    return seen[:limit]


def assess_research_quality(payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload.get('sources', []) or []
    claims = payload.get('claims', []) or []
    failures = payload.get('failures', []) or []
    blocked_sources = payload.get('blocked_sources', []) or []
    source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
    citation_validation = payload.get('citation_validation') or validate_citations(payload)
    citation_audit = payload.get('citation_audit') if isinstance(payload.get('citation_audit'), dict) else audit_citations(payload)
    source_freshness = payload.get('source_freshness') if isinstance(payload.get('source_freshness'), dict) else {}
    evidence_index = payload.get('evidence_index') if isinstance(payload.get('evidence_index'), dict) else {}
    claim_support = payload.get('claim_support') if isinstance(payload.get('claim_support'), dict) else {}

    source_count = len(sources)
    domain_count = int(source_quality.get('unique_domain_count') or 0)
    primary_source_count = int(source_quality.get('primary_source_count') or 0)
    if not domain_count:
        domain_count = len(
            {
                (urlparse(str(source.get('final_url') or source.get('url') or '')).hostname or '').lower()
                for source in sources
                if source.get('final_url') or source.get('url')
            }
            - {''}
        )
    citation_count = int(citation_validation.get('citation_count') or 0)
    multi_source_claims = sum(1 for claim in claims if len(claim.get('supporting_sources', []) or []) >= 2)
    conflicted_claims = sum(1 for claim in claims if claim.get('conflicting_sources'))

    score = 0
    strengths: list[str] = []
    gaps: list[str] = []

    if source_count >= 5:
        score += 25
        strengths.append('Five or more readable sources were collected.')
    elif source_count >= 3:
        score += 18
        strengths.append('At least three readable sources were collected.')
    elif source_count >= 1:
        score += 8
        gaps.append('Readable source coverage is thin.')
    else:
        gaps.append('No readable sources were collected.')

    if domain_count >= 3:
        score += 20
        strengths.append('Sources span at least three domains.')
    elif domain_count >= 2:
        score += 12
        strengths.append('Sources span more than one domain.')
    else:
        gaps.append('Source diversity is low.')

    if citation_validation.get('ok') and citation_count:
        score += 20
        strengths.append('Citations validate against collected sources.')
    elif citation_count:
        score += 8
        gaps.append('Some citations do not match collected sources.')
    else:
        gaps.append('No citations were produced.')

    if citation_audit.get('ok') and claims:
        score += 5
        strengths.append('Claim and report citation audit passed.')
    else:
        uncited_claims = len(citation_audit.get('uncited_claim_ids', []) or [])
        unsupported_sections = len(citation_audit.get('unsupported_report_sections', []) or [])
        if uncited_claims:
            score -= min(10, uncited_claims * 2)
            gaps.append(f'{uncited_claims} claim(s) lack supporting evidence citations.')
        if unsupported_sections:
            score -= min(10, unsupported_sections * 2)
            gaps.append(f'{unsupported_sections} report section(s) lack source citations.')

    if multi_source_claims:
        score += min(20, multi_source_claims * 5)
        strengths.append(f'{multi_source_claims} claim(s) have multi-source support.')
    elif claims:
        score += 6
        gaps.append('Extracted claims are supported by only one source each.')
    else:
        gaps.append('No claim-level statements were extracted.')

    if not failures:
        score += 10
        strengths.append('No read failures were recorded.')
    elif blocked_sources:
        score -= min(15, len(blocked_sources) * 5)
        gaps.append(f'{len(blocked_sources)} source(s) were blocked or required manual access.')

    if conflicted_claims:
        score -= min(15, conflicted_claims * 5)
        gaps.append(f'{conflicted_claims} claim(s) have possible conflicts.')

    if primary_source_count >= 2:
        score += 10
        strengths.append('Multiple strong primary sources were included.')
    elif primary_source_count == 1:
        score += 5
        strengths.append('One strong primary source was included.')
    elif source_count:
        gaps.append('No strong primary source was identified in the selected sources.')

    evidence_coverage = evidence_index.get('coverage') if isinstance(evidence_index.get('coverage'), dict) else {}
    top_chunk_source_count = int(evidence_coverage.get('top_chunk_source_count') or 0)
    top_without_evidence = list(evidence_coverage.get('top_chunk_sources_without_extracted_evidence') or [])
    if evidence_index.get('ok') and top_chunk_source_count >= 2:
        score += 6
        strengths.append('Evidence index found relevant chunks across multiple sources.')
    elif sources:
        gaps.append('Evidence index found little query-relevant source text.')
    if top_without_evidence:
        gaps.append(f'{len(top_without_evidence)} high-relevance source(s) have no extracted evidence quote.')

    indexed_supported_claims = int(claim_support.get('supported_claim_count') or 0)
    indexed_unsupported_claims = int(claim_support.get('unsupported_claim_count') or 0)
    indexed_multi_source_claims = int(claim_support.get('multi_source_supported_claim_count') or 0)
    if indexed_multi_source_claims:
        score += min(12, indexed_multi_source_claims * 4)
        strengths.append(f'{indexed_multi_source_claims} claim(s) have indexed support from multiple sources.')
    elif indexed_supported_claims:
        score += min(8, indexed_supported_claims * 2)
        strengths.append(f'{indexed_supported_claims} claim(s) are tied to indexed evidence chunks.')
    if indexed_unsupported_claims:
        score -= min(12, indexed_unsupported_claims * 3)
        gaps.append(f'{indexed_unsupported_claims} claim(s) lack matching indexed evidence chunks.')

    freshness_gaps = list(source_freshness.get('gaps', []) or [])
    if source_freshness.get('current_sensitive') and source_freshness.get('content_freshness_evidence'):
        score += 5
        strengths.append('Current-sensitive question has content freshness evidence.')
    elif freshness_gaps:
        score -= min(10, len(freshness_gaps) * 3)
        gaps.extend(str(item) for item in freshness_gaps[:3])

    score = max(0, min(100, score))
    if score >= 75:
        label = 'strong'
    elif score >= 50:
        label = 'moderate'
    elif score >= 25:
        label = 'thin'
    else:
        label = 'weak'

    return {
        'label': label,
        'score': score,
        'source_count': source_count,
        'unique_domain_count': domain_count,
        'citation_count': citation_count,
        'primary_source_count': primary_source_count,
        'multi_source_claim_count': multi_source_claims,
        'conflicted_claim_count': conflicted_claims,
        'blocked_source_count': len(blocked_sources),
        'indexed_chunk_count': int(evidence_index.get('chunk_count') or 0),
        'top_chunk_source_count': top_chunk_source_count,
        'indexed_supported_claim_count': indexed_supported_claims,
        'indexed_unsupported_claim_count': indexed_unsupported_claims,
        'indexed_multi_source_claim_count': indexed_multi_source_claims,
        'strengths': strengths,
        'gaps': gaps,
    }


def _repeated_line_warnings(report: str, *, min_words: int = 4, limit: int = 5) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for raw_line in report.splitlines():
        line = ' '.join(raw_line.strip().lower().split())
        if not line or line.startswith('|') or len(line.split()) < min_words:
            continue
        if line in seen and line not in repeated:
            repeated.append(raw_line.strip()[:180])
            if len(repeated) >= limit:
                break
        seen.add(line)
    return repeated


def build_source_policy_audit(payload: dict[str, Any]) -> dict[str, Any]:
    failures = [item for item in payload.get('failures', []) or [] if isinstance(item, dict)]
    selection_trace = [item for item in payload.get('selection_trace', []) or [] if isinstance(item, dict)]
    skipped_by_policy = [
        item
        for item in failures
        if item.get('skip_reason') or item.get('skipped') or str(item.get('message') or '').startswith('skipped by source policy:')
    ]
    recovery_skips = [
        item
        for item in failures
        if item.get('recovery_skipped') or item.get('recovery_skip_reason')
    ]
    trace_policy_skips = [item for item in selection_trace if item.get('decision') == 'skipped_source_policy']
    trace_recovery_skips = [item for item in selection_trace if item.get('recovery_skipped') or item.get('recovery_skip_reason')]
    skip_reason_counts: dict[str, int] = {}
    domains: dict[str, int] = {}
    for item in skipped_by_policy + trace_policy_skips:
        reason = str(item.get('skip_reason') or 'source_policy').strip()
        skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
        url = str(item.get('url') or '')
        domain = (urlparse(url).hostname or '').lower().removeprefix('www.')
        if domain:
            domains[domain] = domains.get(domain, 0) + 1
    hard_block_recovery_skip_count = sum(
        1
        for item in recovery_skips + trace_recovery_skips
        if str(item.get('recovery_skip_reason') or '') == 'hard_block_or_no_recovery_domain'
    )
    samples = []
    seen_samples = set()
    for item in skipped_by_policy + recovery_skips + trace_policy_skips + trace_recovery_skips:
        url = str(item.get('url') or '')
        if not url or url in seen_samples:
            continue
        seen_samples.add(url)
        samples.append(
            {
                'url': url,
                'title': item.get('title'),
                'skip_reason': item.get('skip_reason'),
                'recovery_skip_reason': item.get('recovery_skip_reason'),
                'decision': item.get('decision'),
                'message': item.get('message'),
            }
        )
        if len(samples) >= 8:
            break
    warnings = []
    if skipped_by_policy or trace_policy_skips:
        warnings.append('Some search results were skipped before fetching by source policy.')
    if recovery_skips or trace_recovery_skips:
        warnings.append('Some blocked pages skipped same-domain recovery to avoid slow or futile retries.')
    return {
        'ok': not bool(warnings),
        'skipped_source_count': len(skipped_by_policy),
        'trace_skipped_source_count': len(trace_policy_skips),
        'recovery_skip_count': len(recovery_skips),
        'trace_recovery_skip_count': len(trace_recovery_skips),
        'hard_block_recovery_skip_count': hard_block_recovery_skip_count,
        'skip_reason_counts': skip_reason_counts,
        'skipped_domains': [
            {'domain': domain, 'count': count}
            for domain, count in sorted(domains.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        'samples': samples,
        'warnings': warnings,
    }


def assess_answer_readiness(payload: dict[str, Any], *, report: str | None = None) -> dict[str, Any]:
    """Score whether a research dossier is ready to present as an answer."""
    final_report = str(report if report is not None else payload.get('final_report') or '')
    research_quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else assess_research_quality(payload)
    source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
    research_coverage = payload.get('research_coverage') if isinstance(payload.get('research_coverage'), dict) else {}
    source_freshness = payload.get('source_freshness') if isinstance(payload.get('source_freshness'), dict) else {}
    citation_validation = payload.get('citation_validation') if isinstance(payload.get('citation_validation'), dict) else validate_citations(payload)
    citation_audit = payload.get('citation_audit') if isinstance(payload.get('citation_audit'), dict) else audit_citations(payload, report=final_report)
    final_answer_review = payload.get('final_answer_review') if isinstance(payload.get('final_answer_review'), dict) else {}
    claim_support = payload.get('claim_support') if isinstance(payload.get('claim_support'), dict) else {}
    contradiction_table = payload.get('contradiction_table') if isinstance(payload.get('contradiction_table'), dict) else {}

    score = 100
    blockers: list[str] = []
    warnings: list[str] = []
    strengths: list[str] = []

    quality_score = int(research_quality.get('score') or 0)
    if quality_score >= 75:
        strengths.append('Research quality is strong.')
    elif quality_score >= 50:
        score -= 10
        warnings.append(f"Research quality is only {research_quality.get('label')} ({quality_score}/100).")
    elif quality_score >= 25:
        score -= 25
        warnings.append(f"Research quality is thin ({quality_score}/100).")
    else:
        score -= 40
        blockers.append('Research quality is weak.')

    if not citation_validation.get('ok'):
        score -= 25
        blockers.append('Citation validation failed.')
    elif int(citation_validation.get('citation_count') or 0) > 0:
        strengths.append('Citations validate against collected sources.')
    else:
        score -= 20
        blockers.append('No citations are available for the final answer.')

    if not citation_audit.get('ok'):
        uncited = len(citation_audit.get('uncited_claim_ids', []) or [])
        unsupported = len(citation_audit.get('unsupported_report_sections', []) or [])
        penalty = min(20, (uncited + unsupported) * 4)
        score -= penalty
        warnings.append(f'Citation audit found {uncited} uncited claim(s) and {unsupported} unsupported section(s).')
    else:
        strengths.append('Claim/report citation audit passed.')

    primary_sources = int(source_quality.get('primary_source_count') or 0)
    unique_domains = int(source_quality.get('unique_domain_count') or research_quality.get('unique_domain_count') or 0)
    if primary_sources == 0:
        score -= 12
        warnings.append('No strong primary source was selected.')
    elif primary_sources >= 2:
        strengths.append('Multiple strong primary sources are present.')
    if unique_domains < 2:
        score -= 12
        warnings.append('Source diversity is low.')
    elif unique_domains >= 3:
        strengths.append('Sources are domain-diverse.')

    planned = int(research_coverage.get('planned_intent_count') or 0)
    satisfied = int(research_coverage.get('satisfied_intent_count') or 0)
    if planned and satisfied < planned:
        score -= min(18, (planned - satisfied) * 6)
        warnings.append(f'Research plan coverage is incomplete: {satisfied}/{planned} intent(s) satisfied.')
    elif planned:
        strengths.append('Planned research intents were satisfied.')

    freshness_gaps = list(source_freshness.get('gaps', []) or [])
    if source_freshness.get('current_sensitive') and not source_freshness.get('content_freshness_evidence'):
        score -= 12
        warnings.append('Current-sensitive answer lacks freshness evidence.')
    elif source_freshness.get('current_sensitive'):
        strengths.append('Current-sensitive answer has freshness evidence.')
    elif freshness_gaps:
        score -= min(8, len(freshness_gaps) * 3)
        warnings.extend(str(item) for item in freshness_gaps[:2])

    conflicted_claims = int(
        contradiction_table.get('conflicted_claim_count')
        or research_quality.get('conflicted_claim_count')
        or 0
    )
    if conflicted_claims:
        score -= min(20, conflicted_claims * 8)
        blockers.append(f'{conflicted_claims} contradicted claim(s) still need resolution.')
    else:
        strengths.append('No unresolved source-claim contradictions were identified.')

    unsupported_claims = int(claim_support.get('unsupported_claim_count') or research_quality.get('indexed_unsupported_claim_count') or 0)
    if unsupported_claims:
        score -= min(15, unsupported_claims * 5)
        warnings.append(f'{unsupported_claims} claim(s) lack indexed evidence support.')

    review_high = int(final_answer_review.get('high_count') or 0) + int(final_answer_review.get('critical_count') or 0)
    review_medium = int(final_answer_review.get('medium_count') or 0)
    if review_high:
        score -= min(30, review_high * 12)
        blockers.append(f'Final answer review found {review_high} high/critical issue(s).')
    elif review_medium:
        score -= min(10, review_medium * 3)
        warnings.append(f'Final answer review found {review_medium} medium issue(s).')
    elif final_answer_review:
        strengths.append('Final answer review found no high-severity issues.')

    repeated_lines = _repeated_line_warnings(final_report)
    if repeated_lines:
        score -= min(10, len(repeated_lines) * 3)
        warnings.append(f'Final report repeats {len(repeated_lines)} substantial line(s).')

    if len(final_report.strip()) < 200:
        score -= 10
        warnings.append('Final report is very short for a research answer.')

    score = max(0, min(100, score))
    if blockers:
        ready = False
        label = 'blocked' if score < 50 else 'needs_review'
    elif score >= 80:
        ready = True
        label = 'ready'
    elif score >= 60:
        ready = False
        label = 'needs_review'
    else:
        ready = False
        label = 'not_ready'

    return {
        'ok': ready,
        'label': label,
        'score': score,
        'blockers': blockers,
        'warnings': warnings,
        'strengths': strengths,
        'repeated_lines': repeated_lines,
        'checks': {
            'research_quality_score': quality_score,
            'citation_validation_ok': bool(citation_validation.get('ok')),
            'citation_audit_ok': bool(citation_audit.get('ok')),
            'primary_source_count': primary_sources,
            'unique_domain_count': unique_domains,
            'planned_intent_count': planned,
            'satisfied_intent_count': satisfied,
            'current_sensitive': bool(source_freshness.get('current_sensitive')),
            'freshness_evidence': bool(source_freshness.get('content_freshness_evidence')),
            'conflicted_claim_count': conflicted_claims,
            'unsupported_claim_count': unsupported_claims,
            'final_review_high_or_critical_count': review_high,
            'final_review_medium_count': review_medium,
            'repeated_line_count': len(repeated_lines),
        },
    }


def _claim_lines(claims: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    if not claims:
        return ['- No extracted claims.']
    lines = []
    for claim in claims[:limit]:
        claim_text = claim.get('claim') or ''
        source_ids = ', '.join(f'source:{item}' for item in claim.get('supporting_sources', []) or [])
        confidence = claim.get('confidence') or 'unknown'
        source_suffix = f' Sources: {source_ids}.' if source_ids else ''
        lines.append(f'- {claim_text} Confidence: {confidence}.{source_suffix}')
    return lines


def _source_reliability_lines(sources: list[dict[str, Any]], *, limit: int = 6) -> list[str]:
    if not sources:
        return ['- No readable sources.']
    lines = []
    for source in sources[:limit]:
        source_id = source.get('source_id')
        title = source.get('title') or source.get('final_url') or source.get('url') or 'Untitled source'
        reliability = source.get('reliability') if isinstance(source.get('reliability'), dict) else {}
        source_type = reliability.get('source_type') or 'unknown'
        weight = reliability.get('reliability_weight') or 'unknown'
        credibility = reliability.get('credibility') if isinstance(reliability.get('credibility'), dict) else {}
        credibility_text = ''
        if credibility:
            credibility_text = f" Credibility: {credibility.get('label')} ({credibility.get('score')}/100)."
        caveats = reliability.get('caveats') or []
        caveat_text = f" Caveats: {'; '.join(str(item) for item in caveats[:2])}." if caveats else ''
        lines.append(f'- source:{source_id} {title} Type: {source_type}. Weight: {weight}.{credibility_text}{caveat_text}')
    return lines


def _source_mix_lines(source_quality: dict[str, Any]) -> list[str]:
    type_counts = source_quality.get('source_type_counts') if isinstance(source_quality.get('source_type_counts'), dict) else {}
    weight_counts = source_quality.get('reliability_weight_counts') if isinstance(source_quality.get('reliability_weight_counts'), dict) else {}
    credibility_counts = source_quality.get('credibility_label_counts') if isinstance(source_quality.get('credibility_label_counts'), dict) else {}
    downgrade_reasons = source_quality.get('downgrade_reasons') if isinstance(source_quality.get('downgrade_reasons'), list) else []
    lines = [
        f"- Domains: {source_quality.get('unique_domain_count', 0)} unique.",
        f"- Strong primary sources: {source_quality.get('primary_source_count', 0)}.",
    ]
    if type_counts:
        source_types = ', '.join(f'{key}: {value}' for key, value in sorted(type_counts.items()))
        lines.append(f'- Source types: {source_types}.')
    if weight_counts:
        weights = ', '.join(f'{key}: {value}' for key, value in sorted(weight_counts.items()))
        lines.append(f'- Reliability weights: {weights}.')
    if credibility_counts:
        credibility = ', '.join(f'{key}: {value}' for key, value in sorted(credibility_counts.items()))
        lines.append(f'- Credibility labels: {credibility}.')
    if source_quality.get('average_credibility_score') is not None:
        lines.append(f"- Average credibility score: {source_quality.get('average_credibility_score')}/100.")
    for item in downgrade_reasons[:5]:
        if isinstance(item, dict):
            message = str(item.get('message') or item.get('reason') or '').strip()
            severity = str(item.get('severity') or 'note').strip()
            if message:
                lines.append(f'- Downgrade ({severity}): {message}')
    return lines


def _source_selection_telemetry_lines(telemetry: dict[str, Any]) -> list[str]:
    if not telemetry:
        return ['- No source-selection telemetry was recorded.']
    lines = [
        f"- Planned reads: {telemetry.get('planned_read_count', 0)}.",
        f"- Attempted reads: {telemetry.get('attempted_read_count', 0)}.",
        f"- Selected sources: {telemetry.get('selected_source_count', 0)}.",
        (
            f"- Authority sources: {telemetry.get('selected_authority_source_count', 0)} selected / "
            f"{telemetry.get('planned_authority_source_count', 0)} planned."
        ),
        (
            f"- Low-value/SEO-like candidates planned: "
            f"{telemetry.get('planned_low_value_source_count', 0)}."
        ),
        f"- Policy skips: {telemetry.get('trace_policy_skip_count', 0)} traced / {telemetry.get('planned_policy_skip_count', 0)} planned.",
        f"- Duplicate skips: {telemetry.get('duplicate_skip_count', 0)}.",
        f"- Read failures: {telemetry.get('read_failure_count', 0)}.",
        f"- Cache-hit sources: {telemetry.get('cache_hit_source_count', 0)}.",
    ]
    repeated_domains = telemetry.get('repeated_domains') if isinstance(telemetry.get('repeated_domains'), dict) else {}
    if repeated_domains:
        repeated = ', '.join(f'{domain}: {count}' for domain, count in list(repeated_domains.items())[:5])
        lines.append(f'- Repeated planned domains: {repeated}.')
    reason_counts = telemetry.get('read_selection_reason_counts') if isinstance(telemetry.get('read_selection_reason_counts'), dict) else {}
    if reason_counts:
        reasons = ', '.join(f'{reason}: {count}' for reason, count in sorted(reason_counts.items())[:5])
        lines.append(f'- Read selection reasons: {reasons}.')
    top_reasons = telemetry.get('top_source_score_reasons') if isinstance(telemetry.get('top_source_score_reasons'), list) else []
    if top_reasons:
        reasons = ', '.join(
            f"{item.get('reason')}: {item.get('count')}"
            for item in top_reasons[:5]
            if isinstance(item, dict) and item.get('reason')
        )
        if reasons:
            lines.append(f'- Top ranking signals: {reasons}.')
    return lines


def _remediation_plan_lines(remediation_plan: dict[str, Any]) -> list[str]:
    if not remediation_plan:
        return ['- No remediation plan was recorded.']
    lines = [
        f"- Gap count: {remediation_plan.get('gap_count', 0)}.",
        f"- Action count: {remediation_plan.get('action_count', 0)}.",
    ]
    for gap in remediation_plan.get('gaps', [])[:5]:
        if isinstance(gap, dict):
            lines.append(f"- Gap ({gap.get('severity', 'note')}): {gap.get('code')} - {gap.get('message')}")
    for action in remediation_plan.get('actions', [])[:5]:
        if isinstance(action, dict):
            lines.append(f"- Action ({action.get('gap_code')}): {action.get('query')}")
    return lines


def _coverage_lines(research_coverage: dict[str, Any]) -> list[str]:
    if not research_coverage:
        return ['- No research coverage audit was recorded.']
    lines = [
        (
            f"- Plan coverage: {research_coverage.get('satisfied_intent_count', 0)}/"
            f"{research_coverage.get('planned_intent_count', 0)} intent(s) satisfied."
        ),
        f"- Average intent source quality: {research_coverage.get('average_intent_quality_score', 0)}/100.",
    ]
    missing = research_coverage.get('missing_intents') or []
    if missing:
        lines.append(f"- Missing intents: {', '.join(str(item) for item in missing[:6])}.")
    low_quality = research_coverage.get('low_quality_intents') or []
    if low_quality:
        lines.append(f"- Low-quality intents: {', '.join(str(item) for item in low_quality[:6])}.")
    by_intent = research_coverage.get('by_intent') if isinstance(research_coverage.get('by_intent'), list) else []
    for item in by_intent[:6]:
        if not isinstance(item, dict):
            continue
        signals = ', '.join(str(signal) for signal in item.get('quality_signals', [])[:4])
        signal_text = f' Signals: {signals}.' if signals else ''
        lines.append(
            f"- Intent {item.get('intent')}: {item.get('status')} quality "
            f"{item.get('quality_label', 'unknown')} ({item.get('quality_score', 0)}/100), "
            f"{item.get('matched_source_count', 0)}/{item.get('selected_source_count', 0)} matched source(s).{signal_text}"
        )
    for gap in research_coverage.get('gaps', [])[:3]:
        lines.append(f'- {gap}')
    return lines


def _citation_audit_lines(citation_audit: dict[str, Any]) -> list[str]:
    if not citation_audit:
        return ['- No citation audit was recorded.']
    lines = [
        f"- Status: {'passed' if citation_audit.get('ok') else 'needs review'}.",
        f"- Claims without evidence citations: {len(citation_audit.get('uncited_claim_ids', []) or [])}.",
        f"- Unknown report source IDs: {citation_audit.get('unknown_report_source_ids', [])}.",
    ]
    unsupported = citation_audit.get('unsupported_report_sections') or []
    if unsupported:
        lines.append(f"- Unsupported report sections: {', '.join(str(item) for item in unsupported[:5])}.")
    return lines


def _freshness_lines(source_freshness: dict[str, Any]) -> list[str]:
    if not source_freshness:
        return ['- No freshness audit was recorded.']
    lines = [
        f"- Current-sensitive: {bool(source_freshness.get('current_sensitive'))}.",
        f"- Content freshness evidence: {bool(source_freshness.get('content_freshness_evidence'))}.",
        f"- Newest mentioned year: {source_freshness.get('newest_mentioned_year')}.",
        f"- Recent-change snippets: {source_freshness.get('recent_change_count', 0)}.",
    ]
    for gap in source_freshness.get('gaps', [])[:3]:
        lines.append(f'- {gap}')
    return lines


def _best_evidence_lines(evidence_index: dict[str, Any], *, limit: int = 5) -> list[str]:
    chunks = evidence_index.get('top_chunks') if isinstance(evidence_index.get('top_chunks'), list) else []
    if not chunks:
        return ['- No indexed evidence chunks were available.']
    lines = []
    for chunk in chunks[:limit]:
        if not isinstance(chunk, dict):
            continue
        source_id = chunk.get('source_id')
        score = chunk.get('score')
        text = ' '.join(str(chunk.get('text') or '').split())
        excerpt = text[:220].rstrip()
        terms = ', '.join(str(item) for item in chunk.get('matched_terms', [])[:6])
        term_text = f' Matched: {terms}.' if terms else ''
        lines.append(f"- source:{source_id} score {score}: {excerpt}{term_text}")
    return lines or ['- No indexed evidence chunks were available.']


def _claim_support_for_reports(claims: list[dict[str, Any]], evidence_index: dict[str, Any], claim_support: dict[str, Any]) -> dict[str, Any]:
    if claim_support:
        return claim_support
    if claims and evidence_index:
        return build_claim_support_table(claims, evidence_index)
    return {}


def _claim_support_lines(claim_support: dict[str, Any], *, limit: int = 6) -> list[str]:
    rows = claim_support.get('claims') if isinstance(claim_support.get('claims'), list) else []
    if not rows:
        return ['- No claim support table was available.']
    lines = []
    for row in rows[:limit]:
        claim = str(row.get('claim') or '').strip()
        status = row.get('status') or 'unknown'
        sources = ', '.join(f"source:{item}" for item in row.get('indexed_support_sources', []) or [])
        source_text = sources or 'no indexed source match'
        chunks = []
        for chunk in row.get('support_chunks', [])[:2]:
            if isinstance(chunk, dict):
                chunk_id = chunk.get('chunk_id') or f"source:{chunk.get('source_id')}"
                terms = ', '.join(str(item) for item in chunk.get('matched_terms', [])[:5])
                terms_text = f' terms: {terms}' if terms else ''
                chunks.append(f"{chunk_id}{terms_text}")
        chunk_text = f" Evidence: {'; '.join(chunks)}." if chunks else ''
        lines.append(f'- {claim} Status: {status}. Indexed support: {source_text}.{chunk_text}')
    gaps = [str(item) for item in claim_support.get('gaps', []) or []]
    for gap in gaps[:3]:
        lines.append(f'- Gap: {gap}')
    return lines


def _source_label(source_id: Any, source_by_id: dict[int, dict[str, Any]]) -> str:
    try:
        source_id_int = int(source_id)
    except (TypeError, ValueError):
        return f'source:{source_id}'
    source = source_by_id.get(source_id_int, {})
    title = str(source.get('title') or source.get('final_url') or source.get('url') or '').strip()
    if title:
        return f'source:{source_id_int} {title[:80]}'
    return f'source:{source_id_int}'


def _contradiction_table(
    claims: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    final_answer_review: dict[str, Any],
    *,
    limit: int = 8,
) -> dict[str, Any]:
    source_by_id = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        try:
            source_by_id[int(source.get('source_id'))] = source
        except (TypeError, ValueError):
            continue
    contradiction = (
        final_answer_review.get('contradiction_review')
        if isinstance(final_answer_review.get('contradiction_review'), dict)
        else contradiction_review(claims)
    )
    contested = contradiction.get('contested_claims') if isinstance(contradiction.get('contested_claims'), list) else []
    retrieval_plan = contradiction.get('retrieval_plan') if isinstance(contradiction.get('retrieval_plan'), list) else []
    retrieval_by_claim: dict[str, list[dict[str, Any]]] = {}
    for item in retrieval_plan:
        if isinstance(item, dict):
            retrieval_by_claim.setdefault(str(item.get('claim_id') or ''), []).append(item)
    rows = []
    for item in contested[:limit]:
        if not isinstance(item, dict):
            continue
        claim_id = item.get('claim_id')
        supporting_sources = list(item.get('supporting_sources', []) or [])
        conflicting_sources = list(item.get('conflicting_sources', []) or [])
        reviews = item.get('conflict_reviews') if isinstance(item.get('conflict_reviews'), list) else []
        review_reasons = []
        for review in reviews[:2]:
            if isinstance(review, dict):
                reason = str(review.get('reason') or review.get('verdict') or '').strip()
                if reason:
                    review_reasons.append(reason)
        plan_items = retrieval_by_claim.get(str(claim_id), [])
        rows.append(
            {
                'claim_id': claim_id,
                'claim': str(item.get('claim') or '').strip(),
                'confidence': item.get('confidence') or 'unknown',
                'supporting_sources': supporting_sources,
                'conflicting_sources': conflicting_sources,
                'supporting_source_labels': [_source_label(source_id, source_by_id) for source_id in supporting_sources],
                'conflicting_source_labels': [_source_label(source_id, source_by_id) for source_id in conflicting_sources],
                'review_reasons': review_reasons,
                'retrieval_queries': [str(plan.get('query') or '') for plan in plan_items if isinstance(plan, dict) and plan.get('query')][:3],
                'resolution_status': 'needs_resolution' if conflicting_sources else 'no_conflict',
            }
        )
    return {
        'ok': not rows,
        'conflicted_claim_count': len(rows),
        'rows': rows,
        'follow_up_searches': contradiction.get('follow_up_searches', []) or [],
    }


def _contradiction_table_lines(contradiction_table: dict[str, Any], *, limit: int = 6) -> list[str]:
    rows = contradiction_table.get('rows') if isinstance(contradiction_table.get('rows'), list) else []
    if not rows:
        return ['- No source-claim contradictions were identified.']
    lines = [
        '| Claim | Supporting Sources | Conflicting Sources | Status | Follow-Up |',
        '| --- | --- | --- | --- | --- |',
    ]
    for row in rows[:limit]:
        claim = str(row.get('claim') or '').replace('|', '\\|')
        supporting = '<br>'.join(str(item).replace('|', '\\|') for item in row.get('supporting_source_labels', []) or []) or ''
        conflicting = '<br>'.join(str(item).replace('|', '\\|') for item in row.get('conflicting_source_labels', []) or []) or ''
        follow_up = '<br>'.join(str(item).replace('|', '\\|') for item in row.get('retrieval_queries', [])[:2] or []) or ''
        status = str(row.get('resolution_status') or 'needs_review')
        lines.append(f'| {claim} | {supporting} | {conflicting} | {status} | {follow_up} |')
    return lines


def _final_answer_review_lines(final_answer_review: dict[str, Any]) -> list[str]:
    if not final_answer_review:
        return ['- No final-answer review was recorded.']
    contradiction = (
        final_answer_review.get('contradiction_review')
        if isinstance(final_answer_review.get('contradiction_review'), dict)
        else {}
    )
    lines = [
        f"- Status: {'passed' if final_answer_review.get('ok') else 'needs review'}.",
        f"- Issues: {final_answer_review.get('issue_count', 0)}.",
        (
            f"- Severity counts: critical {final_answer_review.get('critical_count', 0)}, "
            f"high {final_answer_review.get('high_count', 0)}, "
            f"medium {final_answer_review.get('medium_count', 0)}, "
            f"low {final_answer_review.get('low_count', 0)}."
        ),
    ]
    if contradiction:
        lines.append(f"- Contradicted claims: {contradiction.get('conflicted_claim_count', 0)}.")
        for search in contradiction.get('follow_up_searches', [])[:3]:
            lines.append(f'- Contradiction follow-up: {search}')
    for issue in final_answer_review.get('issues', [])[:5]:
        if isinstance(issue, dict):
            lines.append(
                f"- {issue.get('severity', 'note')}: {issue.get('message', issue.get('code', 'review issue'))} "
                f"Fix: {issue.get('suggested_fix', 'Review before relying on the answer.')}"
            )
    return lines


def _answer_readiness_lines(answer_readiness: dict[str, Any]) -> list[str]:
    if not answer_readiness:
        return ['- No answer readiness gate was recorded.']
    lines = [
        f"- Status: {answer_readiness.get('label')} ({answer_readiness.get('score')}/100).",
        f"- Ready to present: {bool(answer_readiness.get('ok'))}.",
    ]
    blockers = [str(item) for item in answer_readiness.get('blockers', []) or []]
    warnings = [str(item) for item in answer_readiness.get('warnings', []) or []]
    strengths = [str(item) for item in answer_readiness.get('strengths', []) or []]
    if blockers:
        lines.append(f"- Blockers: {'; '.join(blockers[:4])}.")
    if warnings:
        lines.append(f"- Warnings: {'; '.join(warnings[:4])}.")
    if strengths:
        lines.append(f"- Strengths: {'; '.join(strengths[:4])}.")
    return lines


def _source_policy_audit_lines(source_policy_audit: dict[str, Any]) -> list[str]:
    if not source_policy_audit:
        return ['- No source-policy audit was recorded.']
    lines = [
        f"- Policy-skipped sources: {source_policy_audit.get('skipped_source_count', 0)}.",
        f"- Trace policy skips: {source_policy_audit.get('trace_skipped_source_count', 0)}.",
        f"- Recovery skips: {source_policy_audit.get('recovery_skip_count', 0)}.",
        f"- Hard-block recovery skips: {source_policy_audit.get('hard_block_recovery_skip_count', 0)}.",
    ]
    reasons = source_policy_audit.get('skip_reason_counts') if isinstance(source_policy_audit.get('skip_reason_counts'), dict) else {}
    if reasons:
        lines.append('- Skip reasons: ' + ', '.join(f'{key}: {value}' for key, value in sorted(reasons.items())) + '.')
    domains = source_policy_audit.get('skipped_domains') if isinstance(source_policy_audit.get('skipped_domains'), list) else []
    if domains:
        domain_text = ', '.join(
            f"{item.get('domain')}: {item.get('count')}"
            for item in domains[:6]
            if isinstance(item, dict)
        )
        if domain_text:
            lines.append(f'- Skipped domains: {domain_text}.')
    for warning in source_policy_audit.get('warnings', [])[:4]:
        lines.append(f'- Warning: {warning}')
    for sample in source_policy_audit.get('samples', [])[:4]:
        if not isinstance(sample, dict):
            continue
        reason = sample.get('skip_reason') or sample.get('recovery_skip_reason') or sample.get('decision') or 'source_policy'
        lines.append(f"- Sample: {reason} {sample.get('url')}")
    return lines


def build_reports(payload: dict[str, Any]) -> dict[str, str]:
    question = str(payload.get('question') or payload.get('query') or 'Research run')
    sources = payload.get('sources', []) or []
    claims = payload.get('claims', []) or []
    uncertainties = payload.get('uncertainties', []) or []
    recent_changes = payload.get('recent_changes', []) or []
    citations = [str(item) for item in payload.get('citations', []) or [] if item]
    citation_validation = payload.get('citation_validation') or validate_citations(payload)
    citation_audit = payload.get('citation_audit') if isinstance(payload.get('citation_audit'), dict) else audit_citations(payload)
    next_searches = payload.get('recommended_next_searches') or recommended_next_searches(payload)
    research_quality = payload.get('research_quality') or assess_research_quality(payload)
    source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
    research_coverage = payload.get('research_coverage') if isinstance(payload.get('research_coverage'), dict) else {}
    source_freshness = payload.get('source_freshness') if isinstance(payload.get('source_freshness'), dict) else {}
    final_answer_review = payload.get('final_answer_review') if isinstance(payload.get('final_answer_review'), dict) else {}
    answer_readiness = payload.get('answer_readiness') if isinstance(payload.get('answer_readiness'), dict) else {}
    source_policy_audit = payload.get('source_policy_audit') if isinstance(payload.get('source_policy_audit'), dict) else build_source_policy_audit(payload)
    source_selection_telemetry = payload.get('source_selection_telemetry') if isinstance(payload.get('source_selection_telemetry'), dict) else {}
    remediation_plan = payload.get('remediation_plan') if isinstance(payload.get('remediation_plan'), dict) else build_research_remediation_plan(payload)
    evidence_index = payload.get('evidence_index') if isinstance(payload.get('evidence_index'), dict) else {}
    claim_support = _claim_support_for_reports(
        claims,
        evidence_index,
        payload.get('claim_support') if isinstance(payload.get('claim_support'), dict) else {},
    )
    contradiction_table = _contradiction_table(claims, sources, final_answer_review)

    quick_answer = [
        f'# {question}',
        '',
        payload.get('message') or 'Research completed.',
        '',
        '## Key Claims',
        *_claim_lines(claims, limit=5),
        '',
        '## Gaps',
        *_line_items([str(item) for item in uncertainties], empty='No uncertainty notes.'),
    ]

    source_table = [
        f'# Sources: {question}',
        '',
        '| ID | Title | URL |',
        '| --- | --- | --- |',
    ]
    if sources:
        for source in sources[:25]:
            source_id = source.get('source_id')
            title = str(source.get('title') or source.get('final_url') or source.get('url') or 'Untitled source').replace('|', '\\|')
            url = str(source.get('final_url') or source.get('url') or '').replace('|', '\\|')
            source_table.append(f'| source:{source_id} | {title} | {url} |')
    else:
        source_table.append('|  | No readable sources. |  |')

    executive_brief = [
        f'# Executive Brief: {question}',
        '',
        '## Bottom Line',
        payload.get('message') or 'Research completed.',
        '',
        '## What We Know',
        *_claim_lines(claims, limit=6),
        '',
        '## Best Evidence',
        *_best_evidence_lines(evidence_index, limit=4),
        '',
        '## Claim Support',
        *_claim_support_lines(claim_support, limit=5),
        '',
        '## Source-Claim Contradictions',
        *_contradiction_table_lines(contradiction_table, limit=4),
        '',
        '## Confidence And Gaps',
        f"- Research quality: {research_quality.get('label')} ({research_quality.get('score')}/100).",
        f"- Citation validation: {'passed' if citation_validation.get('ok') else 'failed'} ({citation_validation.get('citation_count', 0)} citation(s)).",
        *_line_items([str(item) for item in uncertainties], empty='No uncertainty notes.'),
        '',
        '## Citation Audit',
        *_citation_audit_lines(citation_audit),
        '',
        '## Coverage Audit',
        *_coverage_lines(research_coverage),
        '',
        '## Freshness Audit',
        *_freshness_lines(source_freshness),
        '',
        '## Final Answer Review',
        *_final_answer_review_lines(final_answer_review),
        '',
        '## Answer Readiness',
        *_answer_readiness_lines(answer_readiness),
        '',
        '## Source Policy Audit',
        *_source_policy_audit_lines(source_policy_audit),
        '',
        '## Source Selection Telemetry',
        *_source_selection_telemetry_lines(source_selection_telemetry),
        '',
        '## Evidence Remediation Plan',
        *_remediation_plan_lines(remediation_plan),
        '',
        '## Source Reliability',
        *_source_mix_lines(source_quality),
        '',
        *_source_reliability_lines(sources, limit=5),
        '',
        '## Recommended Next Searches',
        *_line_items([str(item) for item in next_searches], empty='No recommended searches.'),
    ]

    long_report = [
        f'# {question}',
        '',
        '## Answer Snapshot',
        payload.get('message') or 'Research completed.',
        '',
        '## Key Claims',
    ]
    long_report.extend(_claim_lines(claims, limit=8))

    long_report.extend(['', '## Best Evidence'])
    long_report.extend(_best_evidence_lines(evidence_index, limit=8))

    long_report.extend(['', '## Claim Support Table'])
    long_report.extend(_claim_support_lines(claim_support, limit=8))

    long_report.extend(['', '## Source-Claim Contradiction Table'])
    long_report.extend(_contradiction_table_lines(contradiction_table, limit=8))

    long_report.extend(['', '## Sources'])
    if sources:
        for source in sources[:12]:
            source_id = source.get('source_id')
            title = source.get('title') or source.get('final_url') or source.get('url') or 'Untitled source'
            url = source.get('final_url') or source.get('url') or ''
            long_report.append(f'- source:{source_id} {title} {url}'.strip())
    else:
        long_report.append('- No readable sources.')

    long_report.extend(['', '## Source Reliability'])
    long_report.extend(_source_mix_lines(source_quality))
    long_report.append('')
    long_report.extend(_source_reliability_lines(sources, limit=8))

    long_report.extend(['', '## Uncertainties'])
    long_report.extend(_line_items([str(item) for item in uncertainties], empty='No uncertainty notes.'))

    long_report.extend(['', '## Research Quality'])
    long_report.append(f"- Label: {research_quality.get('label')}")
    long_report.append(f"- Score: {research_quality.get('score')}/100")
    long_report.extend(_line_items([str(item) for item in research_quality.get('strengths', [])], empty='No quality strengths identified.'))
    long_report.extend(_line_items([str(item) for item in research_quality.get('gaps', [])], empty='No quality gaps identified.'))

    long_report.extend(['', '## Coverage Audit'])
    long_report.extend(_coverage_lines(research_coverage))

    long_report.extend(['', '## Freshness Audit'])
    long_report.extend(_freshness_lines(source_freshness))

    long_report.extend(['', '## Final Answer Review'])
    long_report.extend(_final_answer_review_lines(final_answer_review))

    long_report.extend(['', '## Answer Readiness'])
    long_report.extend(_answer_readiness_lines(answer_readiness))

    long_report.extend(['', '## Source Policy Audit'])
    long_report.extend(_source_policy_audit_lines(source_policy_audit))

    long_report.extend(['', '## Source Selection Telemetry'])
    long_report.extend(_source_selection_telemetry_lines(source_selection_telemetry))

    long_report.extend(['', '## Evidence Remediation Plan'])
    long_report.extend(_remediation_plan_lines(remediation_plan))

    long_report.extend(['', '## Recent Changes'])
    if recent_changes:
        for item in recent_changes[:6]:
            note = item.get('quote') or item.get('text') or item.get('citation') or item
            citation = item.get('citation') if isinstance(item, dict) else None
            suffix = f' ({citation})' if citation else ''
            long_report.append(f'- {note}{suffix}')
    else:
        long_report.append('- No recent-change notes.')

    long_report.extend(['', '## Citation Validation'])
    long_report.append(f"- Status: {'passed' if citation_validation.get('ok') else 'failed'}")
    long_report.extend(_line_items([str(item) for item in citation_validation.get('invalid_citations', [])], empty='No invalid citations.'))
    long_report.extend(['', '## Citation Audit'])
    long_report.extend(_citation_audit_lines(citation_audit))
    long_report.extend(['', '## Citations'])
    long_report.extend(_line_items(citations[:20], empty='No citations.'))
    long_report.extend(['', '## Recommended Next Searches'])
    long_report.extend(_line_items([str(item) for item in next_searches], empty='No recommended searches.'))

    comparison_matrix = [
        f'# Comparison Matrix: {question}',
        '',
        '| Claim | Confidence | Supporting Sources | Indexed Evidence | Conflicting Sources |',
        '| --- | --- | --- | --- | --- |',
    ]
    support_by_claim = {
        str(row.get('claim') or ''): row
        for row in claim_support.get('claims', []) or []
        if isinstance(row, dict)
    }
    if claims:
        for claim in claims[:12]:
            text = str(claim.get('claim') or '').replace('|', '\\|')
            confidence = str(claim.get('confidence') or 'unknown')
            supporting = ', '.join(f'source:{item}' for item in claim.get('supporting_sources', []) or [])
            conflicting = ', '.join(f'source:{item}' for item in claim.get('conflicting_sources', []) or [])
            support_row = support_by_claim.get(str(claim.get('claim') or '')) or {}
            indexed = ', '.join(str(chunk.get('chunk_id') or f"source:{chunk.get('source_id')}") for chunk in support_row.get('support_chunks', [])[:2] if isinstance(chunk, dict))
            comparison_matrix.append(f'| {text} | {confidence} | {supporting} | {indexed} | {conflicting} |')
    else:
        comparison_matrix.append('| No extracted claims. |  |  |  |  |')

    reports = {
        'quick_answer': '\n'.join(str(line) for line in quick_answer) + '\n',
        'source_table': '\n'.join(str(line) for line in source_table) + '\n',
        'executive_brief': '\n'.join(str(line) for line in executive_brief) + '\n',
        'long_report': '\n'.join(str(line) for line in long_report) + '\n',
        'comparison_matrix': '\n'.join(str(line) for line in comparison_matrix) + '\n',
    }
    return reports


def build_research_report(payload: dict[str, Any], *, report_format: str = 'long_report') -> str:
    reports = build_reports(payload)
    return reports[normalize_report_format(report_format)]


async def finalize_report_payload(payload: dict[str, Any], *, report_format: str) -> None:
    from web_research.local_llm import synthesize_research_report

    payload['contradiction_table'] = _contradiction_table(
        payload.get('claims', []) or [],
        payload.get('sources', []) or [],
        payload.get('final_answer_review') if isinstance(payload.get('final_answer_review'), dict) else {},
    )
    payload['source_policy_audit'] = build_source_policy_audit(payload)
    payload['remediation_plan'] = build_research_remediation_plan(payload)
    payload['reports'] = build_reports(payload)
    payload['report_format'] = normalize_report_format(report_format)
    deterministic_report = payload['reports'][payload['report_format']]
    synthesis = await synthesize_research_report(
        payload,
        deterministic_report=deterministic_report,
        report_format=payload['report_format'],
    )
    payload['report_synthesis'] = {key: value for key, value in synthesis.items() if key != 'report'}
    payload['final_report'] = synthesis.get('report') if synthesis.get('used') else deterministic_report
    payload['citation_audit'] = audit_citations(payload, report=str(payload.get('final_report') or ''))
    payload['final_answer_review'] = adversarial_final_answer_review(payload)
    payload['contradiction_table'] = _contradiction_table(
        payload.get('claims', []) or [],
        payload.get('sources', []) or [],
        payload.get('final_answer_review') if isinstance(payload.get('final_answer_review'), dict) else {},
    )
    payload['source_policy_audit'] = build_source_policy_audit(payload)
    payload['remediation_plan'] = build_research_remediation_plan(payload)
    payload['reports'] = build_reports(payload)
    payload['answer_readiness'] = assess_answer_readiness(payload, report=str(payload.get('final_report') or ''))
    payload['remediation_plan'] = build_research_remediation_plan(payload)
    if not synthesis.get('used'):
        payload['final_report'] = payload['reports'][payload['report_format']]
        payload['citation_audit'] = audit_citations(payload, report=str(payload.get('final_report') or ''))
        payload['final_answer_review'] = adversarial_final_answer_review(payload)
        payload['contradiction_table'] = _contradiction_table(
            payload.get('claims', []) or [],
            payload.get('sources', []) or [],
            payload.get('final_answer_review') if isinstance(payload.get('final_answer_review'), dict) else {},
        )
        payload['source_policy_audit'] = build_source_policy_audit(payload)
        payload['remediation_plan'] = build_research_remediation_plan(payload)
        payload['reports'] = build_reports(payload)
        payload['answer_readiness'] = assess_answer_readiness(payload, report=str(payload.get('final_report') or ''))
        payload['remediation_plan'] = build_research_remediation_plan(payload)
        payload['reports'] = build_reports(payload)
        payload['final_report'] = payload['reports'][payload['report_format']]
        payload['citation_audit'] = audit_citations(payload, report=str(payload.get('final_report') or ''))
        payload['answer_readiness'] = assess_answer_readiness(payload, report=str(payload.get('final_report') or ''))
