from __future__ import annotations

from typing import Any


def _question(payload: dict[str, Any]) -> str:
    return str(payload.get('question') or payload.get('query') or '').strip()


def _add_action(
    actions: list[dict[str, Any]],
    *,
    query: str,
    gap_code: str,
    reason: str,
    priority: int,
    intent: str = 'gap_follow_up',
    site: str | None = None,
) -> None:
    normalized = ' '.join(query.split())
    if not normalized:
        return
    action: dict[str, Any] = {
        'query': normalized,
        'intent': intent,
        'gap_code': gap_code,
        'reason': reason,
        'priority': priority,
    }
    if site:
        action['site'] = site
    actions.append(action)


def build_research_remediation_plan(payload: dict[str, Any], *, limit: int = 8) -> dict[str, Any]:
    question = _question(payload)
    source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
    research_quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    coverage = payload.get('research_coverage') if isinstance(payload.get('research_coverage'), dict) else {}
    freshness = payload.get('source_freshness') if isinstance(payload.get('source_freshness'), dict) else {}
    citation_audit = payload.get('citation_audit') if isinstance(payload.get('citation_audit'), dict) else {}
    answer_readiness = payload.get('answer_readiness') if isinstance(payload.get('answer_readiness'), dict) else {}
    selection = payload.get('source_selection_telemetry') if isinstance(payload.get('source_selection_telemetry'), dict) else {}
    claims = payload.get('claims') if isinstance(payload.get('claims'), list) else []
    blocked_sources = payload.get('blocked_sources') if isinstance(payload.get('blocked_sources'), list) else []
    failures = payload.get('failures') if isinstance(payload.get('failures'), list) else []

    gaps: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    def gap(code: str, message: str, severity: str = 'medium') -> None:
        gaps.append({'code': code, 'message': message, 'severity': severity})

    primary_count = int(source_quality.get('primary_source_count') or 0)
    unique_domains = int(source_quality.get('unique_domain_count') or 0)
    source_count = int(source_quality.get('selected_source_count') or len(payload.get('sources', []) or []))
    single_source_claims = [
        claim
        for claim in claims
        if isinstance(claim, dict) and len(claim.get('supporting_sources', []) or []) == 1
    ]
    conflicted_claims = [
        claim
        for claim in claims
        if isinstance(claim, dict) and claim.get('conflicting_sources')
    ]

    has_source_quality_signal = bool(source_quality) or source_count > 0
    if has_source_quality_signal and primary_count == 0:
        gap('missing_primary', 'No strong primary source was selected.', 'high')
        _add_action(
            actions,
            query=f'{question} official documentation primary source report',
            gap_code='missing_primary',
            reason='Find official or primary evidence for the answer.',
            priority=95,
        )
    if source_count and unique_domains < 2:
        gap('domain_diversity_low', 'Selected sources come from too few domains.', 'medium')
        _add_action(
            actions,
            query=f'{question} independent sources comparison corroborating evidence',
            gap_code='domain_diversity_low',
            reason='Diversify evidence beyond the current repeated domain set.',
            priority=80,
        )
    if single_source_claims:
        gap('single_source_claims', f'{len(single_source_claims)} claim(s) have only one supporting source.', 'medium')
        _add_action(
            actions,
            query=f'{question} corroborating evidence multiple independent sources',
            gap_code='single_source_claims',
            reason='Find corroboration for claims currently supported by one source.',
            priority=85,
        )
    if conflicted_claims:
        gap('unresolved_conflicts', f'{len(conflicted_claims)} claim(s) have conflicting sources.', 'high')
        _add_action(
            actions,
            query=f'{question} conflicting evidence comparison',
            gap_code='unresolved_conflicts',
            reason='Resolve source conflicts before synthesis.',
            priority=100,
            intent='contradiction_resolution',
        )
    if blocked_sources or any(isinstance(item, dict) and item.get('blocked') for item in failures):
        gap('blocked_sources', 'Some candidate sources were blocked or required manual access.', 'medium')
        _add_action(
            actions,
            query=f'{question} alternate source mirror official accessible pdf',
            gap_code='blocked_sources',
            reason='Replace blocked sources with accessible alternates.',
            priority=70,
        )
    missing_intents = [str(item) for item in coverage.get('missing_intents', []) or [] if str(item).strip()]
    for intent in missing_intents[:3]:
        gap('missing_intent', f'Missing or unsatisfied research intent: {intent}.', 'medium')
        _add_action(
            actions,
            query=f'{question} {intent.replace("_", " ")} evidence',
            gap_code='missing_intent',
            reason=f'Satisfy missing research intent: {intent}.',
            priority=75,
        )
    if citation_audit and not citation_audit.get('ok'):
        gap('citation_gaps', 'Citation audit found unsupported claims or report sections.', 'high')
        _add_action(
            actions,
            query=f'{question} cited supporting evidence primary source',
            gap_code='citation_gaps',
            reason='Repair citation grounding with directly citable evidence.',
            priority=90,
        )
    if freshness.get('current_sensitive') and not freshness.get('content_freshness_evidence'):
        gap('freshness_gap', 'Current-sensitive question lacks recent-change evidence.', 'medium')
        _add_action(
            actions,
            query=f'{question} latest update changelog announcement 2026',
            gap_code='freshness_gap',
            reason='Find freshness evidence for current-sensitive claims.',
            priority=76,
        )
    if answer_readiness and not answer_readiness.get('ok'):
        gap('answer_not_ready', 'Answer readiness gate is not satisfied.', 'high')
        _add_action(
            actions,
            query=f'{question} authoritative cited evidence complete answer',
            gap_code='answer_not_ready',
            reason='Repair final answer readiness with stronger cited evidence.',
            priority=88,
        )

    planned_low_value = int(selection.get('planned_low_value_source_count') or 0)
    planned_authority = int(selection.get('planned_authority_source_count') or 0)
    selected_authority = int(selection.get('selected_authority_source_count') or 0)
    repeated_domains = selection.get('repeated_domains') if isinstance(selection.get('repeated_domains'), dict) else {}
    policy_skips = int(selection.get('planned_policy_skip_count') or selection.get('trace_policy_skip_count') or 0)
    read_failures = int(selection.get('read_failure_count') or 0)
    if planned_low_value >= max(2, planned_authority):
        gap('seo_heavy_source_mix', 'Search planning included too many low-value or SEO-like candidates.', 'medium')
        _add_action(
            actions,
            query=f'{question} official data report filing benchmark',
            gap_code='seo_heavy_source_mix',
            reason='Replace SEO-heavy candidates with official data, reports, filings, or benchmarks.',
            priority=86,
        )
    if planned_authority and selected_authority == 0:
        gap('authority_candidates_not_selected', 'Authority candidates were planned but none became selected sources.', 'high')
        _add_action(
            actions,
            query=f'{question} primary source only official report data',
            gap_code='authority_candidates_not_selected',
            reason='Retry with primary-source-only constraints.',
            priority=92,
        )
    if repeated_domains:
        gap('repeated_domains', 'Planned reads repeated the same domain too often.', 'low')
        _add_action(
            actions,
            query=f'{question} independent source not same domain',
            gap_code='repeated_domains',
            reason='Diversify away from repeated domains.',
            priority=65,
        )
    if policy_skips:
        gap('source_policy_skips', 'Source policy skipped hostile or low-value candidates.', 'low')
        _add_action(
            actions,
            query=f'{question} accessible official source alternative',
            gap_code='source_policy_skips',
            reason='Replace policy-skipped sources with accessible alternatives.',
            priority=62,
        )
    if read_failures >= 2:
        gap('read_failures', 'Multiple planned reads failed.', 'medium')
        _add_action(
            actions,
            query=f'{question} pdf official accessible source',
            gap_code='read_failures',
            reason='Find more fetchable source formats.',
            priority=68,
        )

    for quality_gap in research_quality.get('gaps', []) or []:
        text = str(quality_gap).strip()
        if not text:
            continue
        gap('research_quality_gap', text, 'medium')

    deduped_actions: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for action in sorted(actions, key=lambda item: (-int(item.get('priority') or 0), str(item.get('query') or ''))):
        query = str(action.get('query') or '')
        normalized = ' '.join(query.lower().split())
        if not normalized or normalized in seen_queries:
            continue
        seen_queries.add(normalized)
        deduped_actions.append(action)
        if len(deduped_actions) >= limit:
            break

    return {
        'ok': not gaps,
        'gap_count': len(gaps),
        'gaps': gaps,
        'actions': deduped_actions,
        'action_count': len(deduped_actions),
    }
