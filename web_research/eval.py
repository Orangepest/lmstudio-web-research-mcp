from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SOURCE_CITATION_RE = re.compile(r'\bsource:\d+\b')
LOW_VALUE_SELECTION_REASONS = {'low_value_hint'}
STRONG_SELECTION_REASONS = {'primary_tld', 'primary_source_hint', 'documentation_or_repository'}


def utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def load_eval_tasks(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding='utf-8'))
    tasks = data.get('tasks') if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        raise ValueError('Eval task file must contain a JSON list or an object with a tasks list.')
    normalized = []
    seen_ids: set[str] = set()
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ValueError(f'Eval task #{index} is not a JSON object.')
        task_id = str(task.get('id') or '').strip()
        question = str(task.get('question') or '').strip()
        if not task_id:
            raise ValueError(f'Eval task #{index} is missing id.')
        if task_id in seen_ids:
            raise ValueError(f'Duplicate eval task id: {task_id}')
        if not question:
            raise ValueError(f'Eval task {task_id} is missing question.')
        seen_ids.add(task_id)
        normalized.append(
            {
                'id': task_id,
                'category': str(task.get('category') or 'general'),
                'question': question,
                'tool': str(task.get('tool') or 'research_web'),
                'params': task.get('params') if isinstance(task.get('params'), dict) else {},
                'expected_domains': [str(item).lower() for item in task.get('expected_domains', []) or []],
                'required_checks': [str(item) for item in task.get('required_checks', []) or []],
                'tags': [str(item) for item in task.get('tags', []) or []],
                'review_notes': str(task.get('review_notes') or ''),
            }
        )
    return normalized


def source_domain(source: dict[str, Any]) -> str:
    url = str(source.get('final_url') or source.get('url') or '')
    return (urlparse(url).hostname or '').lower()


def _domain_matches_expected(domain: str, expected: str) -> bool:
    if domain == expected or domain.endswith(f'.{expected}'):
        return True
    if expected == 'github.com' and domain == 'raw.githubusercontent.com':
        return True
    return False


def _final_report_cites_sources(payload: dict[str, Any]) -> bool:
    report = str(payload.get('final_report') or '')
    return bool(SOURCE_CITATION_RE.search(report))


def _has_blocked_handoff(payload: dict[str, Any]) -> bool:
    if not payload.get('blocked_sources'):
        return True
    if payload.get('manual_visit_links'):
        return True
    for failure in payload.get('failures', []) or []:
        if failure.get('blocked') and failure.get('manual_handoff'):
            return True
    return False


def _report_has_section(payload: dict[str, Any], section: str) -> bool:
    report = str(payload.get('final_report') or '')
    return section.lower() in report.lower()


def score_research_payload(payload: dict[str, Any], task: dict[str, Any] | None = None) -> dict[str, Any]:
    task = task or {}
    sources = payload.get('sources', []) or []
    domains = sorted({source_domain(source) for source in sources if source_domain(source)})
    citation_validation = payload.get('citation_validation') if isinstance(payload.get('citation_validation'), dict) else {}
    citation_audit = payload.get('citation_audit') if isinstance(payload.get('citation_audit'), dict) else {}
    research_coverage = payload.get('research_coverage') if isinstance(payload.get('research_coverage'), dict) else {}
    source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
    source_freshness = payload.get('source_freshness') if isinstance(payload.get('source_freshness'), dict) else {}
    research_quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    final_answer_review = payload.get('final_answer_review') if isinstance(payload.get('final_answer_review'), dict) else {}
    evidence_index = payload.get('evidence_index') if isinstance(payload.get('evidence_index'), dict) else {}
    claim_support = payload.get('claim_support') if isinstance(payload.get('claim_support'), dict) else {}
    agent_loop = payload.get('agent_loop') if isinstance(payload.get('agent_loop'), dict) else {}
    contradiction_table = payload.get('contradiction_table') if isinstance(payload.get('contradiction_table'), dict) else {}
    contradiction_review = (
        final_answer_review.get('contradiction_review')
        if isinstance(final_answer_review.get('contradiction_review'), dict)
        else {}
    )
    credibility_counts = source_quality.get('credibility_label_counts') if isinstance(source_quality.get('credibility_label_counts'), dict) else {}
    expected_domains = [str(item).lower() for item in task.get('expected_domains', []) or []]
    required_checks = [str(item) for item in task.get('required_checks', []) or []]
    matched_expected_domains = [
        expected
        for expected in expected_domains
        if any(_domain_matches_expected(domain, expected) for domain in domains)
    ]
    claim_count = len(payload.get('claims', []) or [])
    indexed_supported_claim_count = int(claim_support.get('supported_claim_count') or 0)
    indexed_unsupported_claim_count = int(claim_support.get('unsupported_claim_count') or 0)
    indexed_multi_source_claim_count = int(claim_support.get('multi_source_supported_claim_count') or 0)
    conflicted_claim_count = int(contradiction_review.get('conflicted_claim_count') or 0)
    contradiction_retrieval_plan_count = len(contradiction_review.get('retrieval_plan', []) or [])
    contradiction_table_rows = (
        contradiction_table.get('rows') if isinstance(contradiction_table.get('rows'), list) else []
    )
    contradiction_table_row_count = len(contradiction_table_rows)
    contradiction_table_supporting_row_count = sum(
        1
        for row in contradiction_table_rows
        if isinstance(row, dict) and (row.get('supporting_sources') or row.get('supporting_source_labels'))
    )
    contradiction_table_conflicting_row_count = sum(
        1
        for row in contradiction_table_rows
        if isinstance(row, dict) and (row.get('conflicting_sources') or row.get('conflicting_source_labels'))
    )
    contradiction_table_resolution_query_count = sum(
        len(row.get('retrieval_queries', []) or [])
        for row in contradiction_table_rows
        if isinstance(row, dict)
    )
    contradiction_table_needs_resolution_count = sum(
        1
        for row in contradiction_table_rows
        if isinstance(row, dict) and row.get('resolution_status') == 'needs_resolution'
    )
    searches = payload.get('searches', []) or []
    contradiction_resolution_search_count = sum(1 for item in searches if isinstance(item, dict) and item.get('intent') == 'contradiction_resolution')
    follow_up_search_count = sum(1 for item in searches if isinstance(item, dict) and item.get('intent') in {'gap_follow_up', 'contradiction_resolution'})
    average_intent_quality_score = float(research_coverage.get('average_intent_quality_score') or 0)
    low_quality_intent_count = len(research_coverage.get('low_quality_intents', []) or [])
    final_high_or_critical = int(final_answer_review.get('high_count') or 0) + int(final_answer_review.get('critical_count') or 0)
    selection_trace = payload.get('selection_trace') if isinstance(payload.get('selection_trace'), list) else []
    read_top_threshold = max(1, int(task.get('params', {}).get('read_top') or task.get('params', {}).get('read_top_per_query') or 2))
    selected_trace = [
        item
        for item in selection_trace
        if isinstance(item, dict) and item.get('decision') in {'selected', 'selected_recovery'}
    ]
    selected_original_ranks = [
        int(item.get('original_rank'))
        for item in selected_trace
        if str(item.get('original_rank') or '').isdigit()
    ]
    selected_low_value_source_count = sum(
        1
        for item in selected_trace
        if LOW_VALUE_SELECTION_REASONS & set(item.get('source_score_reasons', []) or [])
    )
    buried_strong_selected_count = sum(
        1
        for item in selected_trace
        if int(item.get('original_rank') or 0) > read_top_threshold
        and STRONG_SELECTION_REASONS & set(item.get('source_score_reasons', []) or [])
    )
    planned_reads = payload.get('planned_reads') if isinstance(payload.get('planned_reads'), list) else []
    planned_low_value_source_count = sum(
        1
        for item in planned_reads
        if isinstance(item, dict) and LOW_VALUE_SELECTION_REASONS & set(item.get('source_score_reasons', []) or [])
    )

    checks = {
        'has_readable_source': bool(sources),
        'has_three_sources': len(sources) >= 3,
        'has_domain_diversity': len(domains) >= 3,
        'citations_validate': bool(citation_validation.get('ok')) and int(citation_validation.get('citation_count') or 0) > 0,
        'citation_audit_passes': not citation_audit or bool(citation_audit.get('ok')),
        'coverage_audit_present': bool(research_coverage),
        'coverage_intents_satisfied': not research_coverage or not research_coverage.get('missing_intents'),
        'has_primary_source': int(source_quality.get('primary_source_count') or 0) > 0,
        'has_high_or_medium_credibility': not credibility_counts or any(
            int(credibility_counts.get(label) or 0) > 0 for label in ('high', 'medium')
        ),
        'freshness_audit_passes': not source_freshness or not source_freshness.get('gaps'),
        'final_report_cites_source_ids': _final_report_cites_sources(payload),
        'blocked_sources_have_handoff': _has_blocked_handoff(payload),
        'has_recommended_next_searches': bool(payload.get('recommended_next_searches')),
        'final_answer_review_passes': not final_answer_review or bool(final_answer_review.get('ok')),
        'matches_expected_domains': not expected_domains or bool(matched_expected_domains),
        'evidence_index_present': not sources or bool(evidence_index.get('ok')),
        'best_evidence_reported': not sources or _report_has_section(payload, 'Best Evidence'),
        'claim_support_present': not claim_count or bool(claim_support),
        'indexed_claim_support': not claim_count or indexed_supported_claim_count > 0,
        'no_unsupported_indexed_claims': not claim_count or indexed_unsupported_claim_count == 0,
        'multi_source_indexed_claim_support': not claim_count or indexed_multi_source_claim_count > 0,
        'intent_quality_adequate': not research_coverage or average_intent_quality_score >= 50,
        'no_low_quality_intents': not research_coverage or low_quality_intent_count == 0,
        'contradiction_review_present': bool(final_answer_review) and bool(contradiction_review),
        'contradiction_retrieval_planned': conflicted_claim_count == 0 or contradiction_retrieval_plan_count > 0,
        'contradiction_resolution_searched': conflicted_claim_count == 0 or contradiction_resolution_search_count > 0,
        'contradiction_table_reported': bool(contradiction_table) or _report_has_section(payload, 'Source-Claim Contradiction'),
        'contradiction_table_rows_present': conflicted_claim_count == 0 or contradiction_table_row_count >= conflicted_claim_count,
        'contradiction_table_source_pairs_present': conflicted_claim_count == 0
        or (contradiction_table_supporting_row_count > 0 and contradiction_table_conflicting_row_count > 0),
        'contradiction_table_resolution_queries_present': conflicted_claim_count == 0
        or contradiction_table_resolution_query_count > 0,
        'source_selection_avoids_low_value': selected_low_value_source_count == 0,
        'source_selection_reads_buried_strong_sources': not selected_original_ranks
        or max(selected_original_ranks) <= read_top_threshold
        or buried_strong_selected_count > 0,
        'follow_up_rounds_recorded': not agent_loop or bool(agent_loop.get('rounds') or agent_loop.get('decisions')),
        'no_high_or_critical_final_review_issues': not final_answer_review or final_high_or_critical == 0,
    }
    score = 0
    weights = {
        'has_readable_source': 15,
        'has_three_sources': 15,
        'has_domain_diversity': 15,
        'citations_validate': 20,
        'citation_audit_passes': 10,
        'coverage_audit_present': 5,
        'coverage_intents_satisfied': 10,
        'has_primary_source': 10,
        'has_high_or_medium_credibility': 10,
        'freshness_audit_passes': 5,
        'final_report_cites_source_ids': 10,
        'blocked_sources_have_handoff': 10,
        'has_recommended_next_searches': 5,
        'final_answer_review_passes': 10,
        'matches_expected_domains': 10,
        'evidence_index_present': 8,
        'best_evidence_reported': 6,
        'claim_support_present': 8,
        'indexed_claim_support': 10,
        'no_unsupported_indexed_claims': 8,
        'multi_source_indexed_claim_support': 6,
        'intent_quality_adequate': 8,
        'no_low_quality_intents': 6,
        'contradiction_review_present': 4,
        'contradiction_retrieval_planned': 8,
        'contradiction_resolution_searched': 10,
        'contradiction_table_reported': 4,
        'contradiction_table_rows_present': 8,
        'contradiction_table_source_pairs_present': 8,
        'contradiction_table_resolution_queries_present': 6,
        'source_selection_avoids_low_value': 8,
        'source_selection_reads_buried_strong_sources': 8,
        'follow_up_rounds_recorded': 5,
        'no_high_or_critical_final_review_issues': 10,
    }
    for check, passed in checks.items():
        if passed:
            score += weights[check]
    if research_quality.get('label') == 'strong':
        score += 5
    elif research_quality.get('label') == 'weak':
        score -= 5
    required_check_failures = [check for check in required_checks if check in checks and not checks[check]]
    score -= min(30, len(required_check_failures) * 10)
    if final_high_or_critical:
        score -= min(20, final_high_or_critical * 5)
    if indexed_unsupported_claim_count:
        score -= min(15, indexed_unsupported_claim_count * 3)
    if low_quality_intent_count:
        score -= min(12, low_quality_intent_count * 3)
    score_caps = []
    if required_check_failures:
        score_caps.append(
            {
                'cap': 59,
                'reason': 'required_check_failure',
                'checks': required_check_failures,
            }
        )
    if not checks['citation_audit_passes']:
        score_caps.append({'cap': 84, 'reason': 'citation_audit_failed'})
    if not checks['final_answer_review_passes']:
        score_caps.append({'cap': 79, 'reason': 'final_answer_review_failed'})
    if not checks['no_high_or_critical_final_review_issues']:
        score_caps.append({'cap': 79, 'reason': 'high_or_critical_final_review_issue'})
    if conflicted_claim_count and not checks['contradiction_resolution_searched']:
        score_caps.append({'cap': 79, 'reason': 'conflicted_claims_not_resolution_searched'})
    if score_caps:
        score = min(score, min(int(item['cap']) for item in score_caps))
    if not sources:
        score = min(score, 45)
    if not payload.get('ok'):
        score = min(score, 50)
    score = max(0, min(100, score))
    if score >= 80:
        label = 'pass'
    elif score >= 60:
        label = 'borderline'
    else:
        label = 'fail'
    failed_checks = [check for check, passed in checks.items() if not passed]
    return {
        'score': score,
        'label': label,
        'checks': checks,
        'required_check_failures': required_check_failures,
        'weakest_checks': failed_checks[:8],
        'score_caps': score_caps,
        'metrics': {
            'source_count': len(sources),
            'unique_domain_count': len(domains),
            'domains': domains,
            'citation_count': int(citation_validation.get('citation_count') or 0),
            'invalid_citation_count': len(citation_validation.get('invalid_citations', []) or []),
            'citation_audit_issue_count': len(citation_audit.get('issues', []) or []),
            'coverage_missing_intent_count': len(research_coverage.get('missing_intents', []) or []),
            'primary_source_count': int(source_quality.get('primary_source_count') or 0),
            'credibility_label_counts': credibility_counts,
            'average_credibility_score': source_quality.get('average_credibility_score'),
            'freshness_gap_count': len(source_freshness.get('gaps', []) or []),
            'claim_count': claim_count,
            'indexed_supported_claim_count': indexed_supported_claim_count,
            'indexed_unsupported_claim_count': indexed_unsupported_claim_count,
            'indexed_multi_source_claim_count': indexed_multi_source_claim_count,
            'evidence_index_chunk_count': int(evidence_index.get('chunk_count') or 0),
            'blocked_source_count': len(payload.get('blocked_sources', []) or []),
            'final_answer_review_issue_count': int(final_answer_review.get('issue_count') or 0),
            'final_answer_review_high_count': int(final_answer_review.get('high_count') or 0),
            'final_answer_review_critical_count': int(final_answer_review.get('critical_count') or 0),
            'conflicted_claim_count': conflicted_claim_count,
            'contradiction_retrieval_plan_count': contradiction_retrieval_plan_count,
            'contradiction_resolution_search_count': contradiction_resolution_search_count,
            'contradiction_table_row_count': contradiction_table_row_count,
            'contradiction_table_supporting_row_count': contradiction_table_supporting_row_count,
            'contradiction_table_conflicting_row_count': contradiction_table_conflicting_row_count,
            'contradiction_table_resolution_query_count': contradiction_table_resolution_query_count,
            'contradiction_table_needs_resolution_count': contradiction_table_needs_resolution_count,
            'contradiction_table_report_section': _report_has_section(payload, 'Source-Claim Contradiction'),
            'follow_up_search_count': follow_up_search_count,
            'agent_round_count': len(agent_loop.get('rounds', []) or []),
            'average_intent_quality_score': average_intent_quality_score,
            'low_quality_intent_count': low_quality_intent_count,
            'research_quality_label': research_quality.get('label'),
            'research_quality_score': research_quality.get('score'),
            'matched_expected_domains': matched_expected_domains,
            'selected_original_ranks': selected_original_ranks,
            'selected_low_value_source_count': selected_low_value_source_count,
            'planned_low_value_source_count': planned_low_value_source_count,
            'buried_strong_selected_count': buried_strong_selected_count,
        },
    }


def build_eval_record(task: dict[str, Any], payload: dict[str, Any], *, elapsed_seconds: float) -> dict[str, Any]:
    return {
        'task': task,
        'completed_at': utc_timestamp(),
        'elapsed_seconds': round(elapsed_seconds, 2),
        'ok': bool(payload.get('ok')),
        'run_id': payload.get('run_id'),
        'run_path': payload.get('run_path'),
        'final_report_path': payload.get('final_report_path'),
        'score': score_research_payload(payload, task),
    }


def human_review_template(record: dict[str, Any]) -> str:
    task = record.get('task') if isinstance(record.get('task'), dict) else {}
    score = record.get('score') if isinstance(record.get('score'), dict) else {}
    metrics = score.get('metrics') if isinstance(score.get('metrics'), dict) else {}
    lines = [
        f"# Eval Review: {task.get('id', 'unknown')}",
        '',
        f"- Category: {task.get('category', 'general')}",
        f"- Question: {task.get('question', '')}",
        f"- Automatic score: {score.get('label')} ({score.get('score')}/100)",
        f"- Sources: {metrics.get('source_count', 0)} across {metrics.get('unique_domain_count', 0)} domain(s)",
        f"- Research quality: {metrics.get('research_quality_label')} ({metrics.get('research_quality_score')})",
        f"- Claim support: {metrics.get('indexed_supported_claim_count', 0)} supported, {metrics.get('indexed_unsupported_claim_count', 0)} unsupported",
        f"- Intent quality: {metrics.get('average_intent_quality_score', 0)}/100 with {metrics.get('low_quality_intent_count', 0)} low-quality intent(s)",
        (
            f"- Contradictions: {metrics.get('conflicted_claim_count', 0)} conflicted claim(s), "
            f"{metrics.get('contradiction_table_row_count', 0)} table row(s), "
            f"{metrics.get('contradiction_resolution_search_count', 0)} resolution search(es)"
        ),
        '',
        '## Human Review',
        '',
        '- Answer correctness:',
        '- Source quality:',
        '- Missing obvious sources:',
        '- Synthesis quality:',
        '- Failure honesty:',
        '- Notes:',
    ]
    return '\n'.join(lines) + '\n'
