from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _add_issue(
    issues: list[dict[str, Any]],
    *,
    code: str,
    severity: str,
    message: str,
    suggested_fix: str,
) -> None:
    issues.append(
        {
            'code': code,
            'severity': severity,
            'message': message,
            'suggested_fix': suggested_fix,
        }
    )


def _claim_text_for_query(text: str, *, max_words: int = 10) -> str:
    words = [word.strip('.,;:()[]{}"\'') for word in str(text or '').split()]
    words = [word for word in words if word]
    return ' '.join(words[:max_words])


def contradiction_retrieval_queries(claim: dict[str, Any], *, question: str = '', limit: int = 4) -> list[dict[str, Any]]:
    claim_text = str(claim.get('claim') or '').strip()
    query_focus = _claim_text_for_query(claim_text, max_words=12)
    if not query_focus:
        return []
    prefix = f'{question} ' if question else ''
    templates = [
        (
            f'{prefix}{query_focus} conflicting evidence independent verification',
            'Find independent evidence that can adjudicate disputed claim support.',
        ),
        (
            f'{prefix}{query_focus} official clarification primary source',
            'Find official or primary clarification for the disputed claim.',
        ),
        (
            f'{prefix}{query_focus} correction limitation caveat',
            'Find corrections, limitations, or caveats that explain the disagreement.',
        ),
    ]
    if claim.get('conflict_reviews'):
        for review in claim.get('conflict_reviews', [])[:1]:
            reason = str(review.get('reason') if isinstance(review, dict) else review).strip()
            if reason:
                templates.append(
                    (
                        f'{prefix}{_claim_text_for_query(reason, max_words=10)} official evidence',
                        'Search the contradiction review reason for source-backed clarification.',
                    )
                )
    queries = []
    seen = set()
    for query, rationale in templates:
        normalized = ' '.join(query.split())
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        queries.append(
            {
                'query': normalized,
                'intent': 'contradiction_resolution',
                'rationale': rationale,
                'claim_id': claim.get('claim_id'),
                'claim': claim_text,
                'supporting_sources': list(claim.get('supporting_sources', []) or []),
                'conflicting_sources': list(claim.get('conflicting_sources', []) or []),
            }
        )
        if len(queries) >= limit:
            break
    return queries


def contradiction_review(claims: list[Any], *, question: str = '', limit: int = 5) -> dict[str, Any]:
    contested = []
    follow_up_searches: list[str] = []
    retrieval_plan: list[dict[str, Any]] = []
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict) or not claim.get('conflicting_sources'):
            continue
        claim_id = claim.get('claim_id') or index
        claim_text = str(claim.get('claim') or '').strip()
        item = {
            'claim_id': claim_id,
            'claim': claim_text,
            'supporting_sources': list(claim.get('supporting_sources', []) or []),
            'conflicting_sources': list(claim.get('conflicting_sources', []) or []),
            'confidence': claim.get('confidence'),
        }
        if claim.get('conflict_reviews'):
            item['conflict_reviews'] = list(claim.get('conflict_reviews', []) or [])[:3]
        contested.append(item)
        query_focus = _claim_text_for_query(claim_text)
        if query_focus:
            prefix = f'{question} ' if question else ''
            follow_up_searches.append(f'{prefix}{query_focus} conflicting evidence')
            follow_up_searches.append(f'{prefix}{query_focus} official clarification')
        retrieval_plan.extend(contradiction_retrieval_queries(item, question=question, limit=3))

    seen_searches = []
    for search in follow_up_searches:
        normalized = ' '.join(search.split())
        if normalized and normalized.lower() not in {item.lower() for item in seen_searches}:
            seen_searches.append(normalized)

    return {
        'ok': not contested,
        'conflicted_claim_count': len(contested),
        'contested_claims': contested[:limit],
        'follow_up_searches': seen_searches[:limit],
        'retrieval_plan': retrieval_plan[:limit],
    }


def adversarial_final_answer_review(payload: dict[str, Any]) -> dict[str, Any]:
    sources = _as_list(payload.get('sources'))
    claims = _as_list(payload.get('claims'))
    question = str(payload.get('question') or payload.get('query') or '').strip()
    blocked_sources = _as_list(payload.get('blocked_sources'))
    recommended_next_searches = _as_list(payload.get('recommended_next_searches'))
    research_quality = _as_dict(payload.get('research_quality'))
    source_quality = _as_dict(payload.get('source_quality'))
    research_coverage = _as_dict(payload.get('research_coverage'))
    source_freshness = _as_dict(payload.get('source_freshness'))
    citation_audit = _as_dict(payload.get('citation_audit'))
    citation_validation = _as_dict(payload.get('citation_validation'))
    contradiction = contradiction_review(claims, question=question)

    issues: list[dict[str, Any]] = []
    if not sources:
        _add_issue(
            issues,
            code='no_readable_sources',
            severity='critical',
            message='No readable sources were selected for the final answer.',
            suggested_fix='Run additional searches or manual source reads before relying on the answer.',
        )
    if citation_validation and not citation_validation.get('ok'):
        _add_issue(
            issues,
            code='citation_validation_failed',
            severity='critical',
            message='One or more final-answer citations do not validate against collected sources.',
            suggested_fix='Remove invalid citations or regenerate the report from validated source IDs.',
        )
    if citation_audit and not citation_audit.get('ok'):
        _add_issue(
            issues,
            code='citation_audit_failed',
            severity='high',
            message='The final report has claim/report citation audit issues.',
            suggested_fix='Add citations to unsupported claims and remove unsupported report sections.',
        )
    missing_intents = _as_list(research_coverage.get('missing_intents'))
    if missing_intents:
        _add_issue(
            issues,
            code='coverage_gaps',
            severity='high',
            message=f'Research plan intents remain unsatisfied: {", ".join(str(item) for item in missing_intents[:5])}.',
            suggested_fix='Run targeted follow-up searches for the missing intents before finalizing.',
        )
    freshness_gaps = _as_list(source_freshness.get('gaps'))
    if freshness_gaps:
        _add_issue(
            issues,
            code='freshness_gaps',
            severity='medium',
            message=f'Freshness audit found {len(freshness_gaps)} gap(s).',
            suggested_fix='Search for current official announcements, changelogs, or date-stamped updates.',
        )
    if int(source_quality.get('primary_source_count') or 0) == 0 and sources:
        _add_issue(
            issues,
            code='no_primary_sources',
            severity='high',
            message='No strong primary sources were identified in the selected sources.',
            suggested_fix='Search official documentation, government, academic, repository, or vendor sources.',
        )
    if int(source_quality.get('unique_domain_count') or 0) < 2 and sources:
        _add_issue(
            issues,
            code='low_domain_diversity',
            severity='medium',
            message='Selected sources do not span multiple domains.',
            suggested_fix='Add independent sources from different domains to reduce single-site bias.',
        )
    single_source_claims = [
        claim.get('claim_id') or index
        for index, claim in enumerate(claims, start=1)
        if isinstance(claim, dict) and len(claim.get('supporting_sources', []) or []) == 1
    ]
    if single_source_claims:
        _add_issue(
            issues,
            code='single_source_claims',
            severity='medium',
            message=f'{len(single_source_claims)} claim(s) are supported by only one source.',
            suggested_fix='Find corroborating sources or lower confidence for single-source claims.',
        )
    conflicted_claims = [item.get('claim_id') for item in contradiction.get('contested_claims', [])]
    if conflicted_claims:
        _add_issue(
            issues,
            code='conflicted_claims',
            severity='high',
            message=f'{len(conflicted_claims)} claim(s) have conflicting sources.',
            suggested_fix='Run contradiction-focused searches and present the disagreement explicitly.',
        )
    if blocked_sources:
        _add_issue(
            issues,
            code='blocked_sources',
            severity='medium',
            message=f'{len(blocked_sources)} source(s) were blocked or require manual access.',
            suggested_fix='Use manual visit links or alternate accessible sources before treating the answer as complete.',
        )
    if research_quality.get('label') in {'weak', 'thin'}:
        _add_issue(
            issues,
            code='low_research_quality',
            severity='high' if research_quality.get('label') == 'weak' else 'medium',
            message=f"Research quality is {research_quality.get('label')} ({research_quality.get('score')}/100).",
            suggested_fix='Continue the research run until source coverage, citations, and claim support improve.',
        )
    if issues and not recommended_next_searches:
        _add_issue(
            issues,
            code='missing_follow_up_plan',
            severity='low',
            message='The answer has review issues but no recommended next searches.',
            suggested_fix='Generate targeted follow-up searches for the highest-severity review issues.',
        )

    severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    issues.sort(key=lambda item: (severity_order.get(str(item.get('severity')), 99), str(item.get('code'))))
    return {
        'ok': not any(issue.get('severity') in {'critical', 'high'} for issue in issues),
        'issue_count': len(issues),
        'critical_count': sum(1 for issue in issues if issue.get('severity') == 'critical'),
        'high_count': sum(1 for issue in issues if issue.get('severity') == 'high'),
        'medium_count': sum(1 for issue in issues if issue.get('severity') == 'medium'),
        'low_count': sum(1 for issue in issues if issue.get('severity') == 'low'),
        'issues': issues,
        'contradiction_review': contradiction,
    }
