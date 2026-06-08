from __future__ import annotations

import re
from typing import Any


SOURCE_REF_RE = re.compile(r'\bsource:(\d+)(?:\[[^\]]+\])?')


def _source_ids(payload: dict[str, Any]) -> set[int]:
    ids = set()
    for source in payload.get('sources', []) or []:
        try:
            ids.add(int(source.get('source_id')))
        except (TypeError, ValueError):
            continue
    return ids


def _citation_source_id(citation: str) -> int | None:
    match = SOURCE_REF_RE.search(citation)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def validate_citations(payload: dict[str, Any]) -> dict[str, Any]:
    source_ids = _source_ids(payload)
    citations = [str(item) for item in payload.get('citations', []) or [] if item]
    invalid = []
    for citation in citations:
        source_id = _citation_source_id(citation)
        if source_id is None or source_id not in source_ids:
            invalid.append(citation)
    return {
        'ok': not invalid,
        'citation_count': len(citations),
        'invalid_citations': invalid,
        'source_count': len(source_ids),
    }


def _claim_citations(claim: dict[str, Any]) -> list[str]:
    citations = []
    for item in claim.get('supporting_evidence', []) or []:
        citation = item.get('citation') if isinstance(item, dict) else None
        if citation:
            citations.append(str(citation))
    return citations


def _report_section_citation_status(report: str) -> list[dict[str, Any]]:
    sections = []
    current_heading = 'Preamble'
    current_lines = []
    for line in report.splitlines():
        if line.startswith('#'):
            if current_lines:
                text = '\n'.join(current_lines).strip()
                sections.append(
                    {
                        'heading': current_heading,
                        'has_citation': bool(SOURCE_REF_RE.search(text)),
                        'char_count': len(text),
                    }
                )
            current_heading = line.lstrip('#').strip() or 'Untitled'
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        text = '\n'.join(current_lines).strip()
        sections.append(
            {
                'heading': current_heading,
                'has_citation': bool(SOURCE_REF_RE.search(text)),
                'char_count': len(text),
            }
        )
    return [section for section in sections if section['char_count'] > 40]


def audit_citations(payload: dict[str, Any], *, report: str | None = None) -> dict[str, Any]:
    validation = payload.get('citation_validation') if isinstance(payload.get('citation_validation'), dict) else validate_citations(payload)
    source_ids = _source_ids(payload)
    claims = [claim for claim in payload.get('claims', []) or [] if isinstance(claim, dict)]
    uncited_claim_ids = []
    claim_citation_map = []
    invalid_claim_citations = []
    for index, claim in enumerate(claims, start=1):
        claim_id = claim.get('claim_id') or index
        citations = _claim_citations(claim)
        supporting_sources = {int(item) for item in claim.get('supporting_sources', []) or [] if str(item).isdigit()}
        cited_source_ids = {
            source_id
            for source_id in (_citation_source_id(citation) for citation in citations)
            if source_id is not None
        }
        invalid = [citation for citation in citations if (_citation_source_id(citation) not in source_ids)]
        if invalid:
            invalid_claim_citations.extend(invalid)
        if not citations:
            uncited_claim_ids.append(claim_id)
        claim_citation_map.append(
            {
                'claim_id': claim_id,
                'citation_count': len(citations),
                'supporting_source_ids': sorted(supporting_sources),
                'cited_source_ids': sorted(cited_source_ids),
                'missing_citation_source_ids': sorted(supporting_sources - cited_source_ids),
                'invalid_citations': invalid,
            }
        )

    report_text = report if report is not None else str(payload.get('final_report') or '')
    report_cited_ids = {
        int(match.group(1))
        for match in SOURCE_REF_RE.finditer(report_text)
    }
    unknown_report_source_ids = sorted(report_cited_ids - source_ids)
    report_sections = _report_section_citation_status(report_text)
    unsupported_sections = [
        section['heading']
        for section in report_sections
        if not section['has_citation']
        and section['heading'].lower()
        not in {
            'answer snapshot',
            'bottom line',
            'confidence and gaps',
            'coverage audit',
            'final answer review',
            'research quality',
            'recommended next searches',
            'uncertainties',
        }
    ]

    issues = []
    if not validation.get('ok'):
        issues.append('Payload citation validation failed.')
    if uncited_claim_ids:
        issues.append(f'{len(uncited_claim_ids)} claim(s) have no supporting evidence citations.')
    if invalid_claim_citations:
        issues.append(f'{len(invalid_claim_citations)} claim citation(s) reference missing sources.')
    if unknown_report_source_ids:
        issues.append(f'Report cites unknown source IDs: {unknown_report_source_ids}.')
    if unsupported_sections:
        issues.append(f'Report sections without source citations: {", ".join(unsupported_sections[:5])}.')

    return {
        'ok': not issues,
        'issues': issues,
        'claim_count': len(claims),
        'uncited_claim_ids': uncited_claim_ids,
        'invalid_claim_citations': invalid_claim_citations,
        'claim_citation_map': claim_citation_map,
        'report_cited_source_ids': sorted(report_cited_ids),
        'unknown_report_source_ids': unknown_report_source_ids,
        'unsupported_report_sections': unsupported_sections,
    }
