from __future__ import annotations

import json
import re
from typing import Any

import httpx

from web_research.config import settings
from web_research.citations import audit_citations


SOURCE_ID_RE = re.compile(r'\bsource:(\d+)\b')


def _claim_terms(claim: str) -> set[str]:
    return {
        token
        for token in re.findall(r'[a-z0-9]+', claim.lower())
        if len(token) > 2
        and token
        not in {
            'and',
            'are',
            'but',
            'for',
            'from',
            'not',
            'the',
            'this',
            'that',
            'with',
        }
    }


def _candidate_pairs(claims: list[dict[str, Any]], *, max_pairs: int = 12) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for index, claim_a in enumerate(claims):
        terms_a = _claim_terms(str(claim_a.get('claim') or ''))
        if not terms_a:
            continue
        for claim_b in claims[index + 1 :]:
            terms_b = _claim_terms(str(claim_b.get('claim') or ''))
            if not terms_b:
                continue
            overlap = len(terms_a & terms_b) / max(1, min(len(terms_a), len(terms_b)))
            already_flagged = bool(
                set(claim_a.get('conflicting_sources', []) or []) & set(claim_b.get('supporting_sources', []) or [])
            ) or bool(set(claim_b.get('conflicting_sources', []) or []) & set(claim_a.get('supporting_sources', []) or []))
            if overlap >= 0.45 or already_flagged:
                scored.append((overlap + (0.5 if already_flagged else 0), claim_a, claim_b))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [(claim_a, claim_b) for _, claim_a, claim_b in scored[:max_pairs]]


def _extract_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


async def _select_model(client: httpx.AsyncClient) -> str:
    if settings.local_llm_model != 'auto':
        return settings.local_llm_model
    response = await client.get('/models')
    response.raise_for_status()
    data = response.json()
    models = data.get('data') if isinstance(data, dict) else None
    if isinstance(models, list) and models:
        model_id = models[0].get('id') if isinstance(models[0], dict) else None
        if model_id:
            return str(model_id)
    return 'local-model'


async def _judge_pair(client: httpx.AsyncClient, model: str, claim_a: str, claim_b: str) -> dict[str, Any] | None:
    prompt = (
        'Decide whether these two research claims contradict each other. '
        'Return only JSON with keys verdict and reason. '
        'verdict must be one of contradiction, compatible, or unsure.\n\n'
        f'Claim A: {claim_a}\n'
        f'Claim B: {claim_b}'
    )
    response = await client.post(
        '/chat/completions',
        json={
            'model': model,
            'messages': [
                {
                    'role': 'system',
                    'content': 'You are a careful local research verifier. Prefer unsure when the claims can both be true.',
                },
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0,
            'max_tokens': 180,
        },
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get('choices') if isinstance(payload, dict) else None
    if not choices:
        return None
    message = choices[0].get('message') if isinstance(choices[0], dict) else None
    content = message.get('content') if isinstance(message, dict) else None
    if not isinstance(content, str):
        return None
    parsed = _extract_json(content)
    if not parsed:
        return None
    verdict = str(parsed.get('verdict') or '').strip().lower()
    if verdict not in {'contradiction', 'compatible', 'unsure'}:
        verdict = 'unsure'
    return {'verdict': verdict, 'reason': str(parsed.get('reason') or '').strip()}


def _append_note(claim: dict[str, Any], note: str) -> None:
    notes = claim.setdefault('source_quality_notes', [])
    if note not in notes:
        notes.append(note)


def _mark_conflict(claim_a: dict[str, Any], claim_b: dict[str, Any], *, reason: str) -> None:
    sources_a = set(claim_a.get('supporting_sources', []) or [])
    sources_b = set(claim_b.get('supporting_sources', []) or [])
    claim_a['conflicting_sources'] = sorted(set(claim_a.get('conflicting_sources', []) or []) | sources_b)
    claim_b['conflicting_sources'] = sorted(set(claim_b.get('conflicting_sources', []) or []) | sources_a)
    claim_a['confidence'] = 'low'
    claim_b['confidence'] = 'low'
    review_a = claim_a.setdefault('conflict_reviews', [])
    review_b = claim_b.setdefault('conflict_reviews', [])
    review_a.append({'method': 'local_llm', 'against_claim_id': claim_b.get('claim_id'), 'verdict': 'contradiction', 'reason': reason})
    review_b.append({'method': 'local_llm', 'against_claim_id': claim_a.get('claim_id'), 'verdict': 'contradiction', 'reason': reason})
    _append_note(claim_a, 'Local LLM judged this claim as conflicting with another claim.')
    _append_note(claim_b, 'Local LLM judged this claim as conflicting with another claim.')


async def review_claim_contradictions(claims: list[dict[str, Any]]) -> dict[str, Any]:
    if not settings.local_llm_contradiction_review:
        return {'enabled': False, 'reviewed_pairs': 0, 'contradictions': 0, 'message': 'Local LLM contradiction review disabled.'}
    pairs = _candidate_pairs(claims)
    if not pairs:
        return {'enabled': True, 'reviewed_pairs': 0, 'contradictions': 0, 'message': 'No likely contradiction pairs found.'}
    try:
        async with httpx.AsyncClient(base_url=settings.local_llm_base_url, timeout=settings.local_llm_timeout) as client:
            model = await _select_model(client)
            reviewed_pairs = 0
            contradictions = 0
            for claim_a, claim_b in pairs:
                result = await _judge_pair(client, model, str(claim_a.get('claim') or ''), str(claim_b.get('claim') or ''))
                if not result:
                    continue
                reviewed_pairs += 1
                if result['verdict'] == 'contradiction':
                    contradictions += 1
                    _mark_conflict(claim_a, claim_b, reason=result.get('reason') or 'No reason returned.')
            return {
                'enabled': True,
                'reviewed_pairs': reviewed_pairs,
                'contradictions': contradictions,
                'model': model,
                'message': 'Local LLM contradiction review completed.',
            }
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return {
            'enabled': True,
            'reviewed_pairs': 0,
            'contradictions': 0,
            'message': f'Local LLM contradiction review unavailable: {exc}',
        }


def _compact_sources(payload: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    sources = []
    for source in (payload.get('sources', []) or [])[:limit]:
        sources.append(
            {
                'source_id': source.get('source_id'),
                'title': source.get('title') or source.get('final_url') or source.get('url'),
                'url': source.get('final_url') or source.get('url'),
            }
        )
    return sources


def _compact_claims(payload: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    claims = []
    for claim in (payload.get('claims', []) or [])[:limit]:
        claims.append(
            {
                'claim': claim.get('claim'),
                'confidence': claim.get('confidence'),
                'supporting_sources': claim.get('supporting_sources', []),
                'conflicting_sources': claim.get('conflicting_sources', []),
            }
        )
    return claims


def _compact_evidence(payload: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    evidence = []
    for item in (payload.get('evidence', []) or [])[:limit]:
        quote = str(item.get('quote') or item.get('text') or '')
        evidence.append(
            {
                'source_id': item.get('source_id'),
                'citation': item.get('citation'),
                'title': item.get('title'),
                'quote': quote[:500],
            }
        )
    return evidence


def _report_synthesis_prompt(payload: dict[str, Any], deterministic_report: str, report_format: str) -> str:
    question = str(payload.get('question') or payload.get('query') or 'Research run')
    context = {
        'question': question,
        'requested_report_format': report_format,
        'message': payload.get('message'),
        'research_quality': payload.get('research_quality'),
        'citation_validation': payload.get('citation_validation'),
        'sources': _compact_sources(payload),
        'claims': _compact_claims(payload),
        'uncertainties': payload.get('uncertainties', []) or [],
        'recent_changes': payload.get('recent_changes', []) or [],
        'source_freshness': payload.get('source_freshness'),
        'recommended_next_searches': payload.get('recommended_next_searches', []) or [],
        'evidence': _compact_evidence(payload),
    }
    return (
        'Rewrite the deterministic research report into a polished Markdown deliverable. '
        'Use only the provided structured evidence. Do not invent facts, dates, URLs, quotes, or citations. '
        'Cite source IDs exactly as source:1, source:2, etc. Preserve uncertainty and blocked-source caveats. '
        'If evidence is thin, say so clearly. Keep the report concise and decision-useful.\n\n'
        f'Structured evidence JSON:\n{json.dumps(context, ensure_ascii=False)}\n\n'
        f'Deterministic fallback report:\n{deterministic_report[:8000]}'
    )


def validate_synthesized_report(report: str, payload: dict[str, Any]) -> dict[str, Any]:
    source_ids = {int(source.get('source_id')) for source in payload.get('sources', []) or [] if source.get('source_id') is not None}
    cited_ids = {int(match.group(1)) for match in SOURCE_ID_RE.finditer(report)}
    unknown_ids = sorted(cited_ids - source_ids)
    citation_validation = payload.get('citation_validation') if isinstance(payload.get('citation_validation'), dict) else {}
    research_quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    issues = []
    if unknown_ids:
        issues.append(f'Synthesized report cites unknown source IDs: {unknown_ids}.')
    citation_audit = audit_citations(payload, report=report)
    if citation_audit.get('unknown_report_source_ids'):
        issues.append(f"Synthesized report citation audit found unknown source IDs: {citation_audit.get('unknown_report_source_ids')}.")
    if int(citation_validation.get('citation_count') or 0) > 0 and not cited_ids:
        issues.append('Synthesized report dropped all source ID citations.')
    if research_quality.get('label') in {'weak', 'thin'}:
        lowered = report.lower()
        if not any(term in lowered for term in ('uncertain', 'uncertainty', 'gap', 'thin', 'weak', 'limited')):
            issues.append('Synthesized report omits uncertainty language despite weak/thin research quality.')
    return {
        'ok': not issues,
        'issues': issues,
        'cited_source_ids': sorted(cited_ids),
        'unknown_source_ids': unknown_ids,
    }


async def synthesize_research_report(payload: dict[str, Any], *, deterministic_report: str, report_format: str) -> dict[str, Any]:
    if not settings.local_llm_report_synthesis:
        return {
            'enabled': False,
            'used': False,
            'message': 'Local LLM report synthesis disabled.',
        }
    try:
        async with httpx.AsyncClient(base_url=settings.local_llm_base_url, timeout=settings.local_llm_timeout) as client:
            model = await _select_model(client)
            response = await client.post(
                '/chat/completions',
                json={
                    'model': model,
                    'messages': [
                        {
                            'role': 'system',
                            'content': (
                                'You are a careful local research report writer. '
                                'You must stay grounded in provided evidence and preserve source IDs.'
                            ),
                        },
                        {'role': 'user', 'content': _report_synthesis_prompt(payload, deterministic_report, report_format)},
                    ],
                    'temperature': 0.2,
                    'max_tokens': settings.local_llm_report_max_tokens,
                },
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get('choices') if isinstance(data, dict) else None
            message = choices[0].get('message') if choices and isinstance(choices[0], dict) else None
            content = message.get('content') if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                return {
                    'enabled': True,
                    'used': False,
                    'model': model,
                    'message': 'Local LLM report synthesis returned no content.',
                }
            report = content.strip() + '\n'
            validation = validate_synthesized_report(report, payload)
            if not validation['ok']:
                return {
                    'enabled': True,
                    'used': False,
                    'model': model,
                    'message': 'Local LLM report synthesis rejected by validation.',
                    'validation': validation,
                }
            return {
                'enabled': True,
                'used': True,
                'model': model,
                'message': 'Local LLM report synthesis completed.',
                'validation': validation,
                'report': report,
            }
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return {
            'enabled': True,
            'used': False,
            'message': f'Local LLM report synthesis unavailable: {exc}',
        }


def _campaign_synthesis_prompt(synthesis: dict[str, Any], deterministic_dossier: str) -> str:
    context = {
        'campaign_id': synthesis.get('manifest', {}).get('campaign_id') if isinstance(synthesis.get('manifest'), dict) else None,
        'objective': synthesis.get('manifest', {}).get('objective') if isinstance(synthesis.get('manifest'), dict) else None,
        'counts': synthesis.get('manifest', {}).get('counts') if isinstance(synthesis.get('manifest'), dict) else {},
        'missing_runs': synthesis.get('missing_runs', []) or [],
        'sources': [
            {
                'source_id': source.get('campaign_source_id'),
                'title': source.get('title'),
                'url': source.get('url'),
                'source_type': source.get('source_type'),
                'reliability_weight': source.get('reliability_weight'),
            }
            for source in (synthesis.get('sources', []) or [])[:25]
            if isinstance(source, dict)
        ],
        'claims': [
            {
                'claim': claim.get('claim'),
                'confidence': claim.get('confidence'),
                'supporting_sources': claim.get('supporting_campaign_sources'),
                'conflicting_sources': claim.get('conflicting_campaign_sources'),
                'run_id': claim.get('run_id'),
            }
            for claim in (synthesis.get('claims', []) or [])[:30]
            if isinstance(claim, dict)
        ],
        'run_audits': [
            {
                'run_id': run.get('run_id'),
                'query': run.get('query'),
                'research_quality_label': run.get('research_quality_label'),
                'research_quality_score': run.get('research_quality_score'),
                'source_count': run.get('source_count'),
                'claim_count': run.get('claim_count'),
            }
            for run in (synthesis.get('audit', {}).get('runs', []) if isinstance(synthesis.get('audit'), dict) else [])[:20]
            if isinstance(run, dict)
        ],
    }
    return (
        'Rewrite the deterministic campaign dossier into a polished Markdown final report. '
        'Use only the provided campaign evidence. Do not invent facts, dates, URLs, quotes, or citations. '
        'Cite campaign source IDs exactly as source:1, source:2, etc. Preserve uncertainty, missing-run caveats, and disagreements. '
        'Open with a decision-useful executive synthesis, then cover evidence, disagreements, source quality, and next research gaps.\n\n'
        f'Campaign evidence JSON:\n{json.dumps(context, ensure_ascii=False)}\n\n'
        f'Deterministic fallback dossier:\n{deterministic_dossier[:12000]}'
    )


def validate_synthesized_campaign_dossier(report: str, synthesis: dict[str, Any]) -> dict[str, Any]:
    source_ids = {
        int(source.get('campaign_source_id'))
        for source in synthesis.get('sources', []) or []
        if isinstance(source, dict) and source.get('campaign_source_id') is not None
    }
    cited_ids = {int(match.group(1)) for match in SOURCE_ID_RE.finditer(report)}
    unknown_ids = sorted(cited_ids - source_ids)
    issues = []
    if unknown_ids:
        issues.append(f'Synthesized campaign dossier cites unknown source IDs: {unknown_ids}.')
    if source_ids and synthesis.get('claim_count') and not cited_ids:
        issues.append('Synthesized campaign dossier dropped all source ID citations.')
    if synthesis.get('missing_runs'):
        lowered = report.lower()
        if not any(term in lowered for term in ('missing', 'incomplete', 'gap', 'not completed', 'limited')):
            issues.append('Synthesized campaign dossier omits missing or incomplete run caveats.')
    return {
        'ok': not issues,
        'issues': issues,
        'cited_source_ids': sorted(cited_ids),
        'unknown_source_ids': unknown_ids,
    }


async def synthesize_campaign_dossier(
    synthesis: dict[str, Any],
    *,
    deterministic_dossier: str,
    enabled: bool | None = None,
) -> dict[str, Any]:
    synthesis_enabled = settings.local_llm_report_synthesis if enabled is None else bool(enabled)
    if not synthesis_enabled:
        return {
            'enabled': False,
            'used': False,
            'message': 'Local LLM campaign synthesis disabled.',
        }
    try:
        async with httpx.AsyncClient(base_url=settings.local_llm_base_url, timeout=settings.local_llm_timeout) as client:
            model = await _select_model(client)
            response = await client.post(
                '/chat/completions',
                json={
                    'model': model,
                    'messages': [
                        {
                            'role': 'system',
                            'content': (
                                'You are a careful local research campaign report writer. '
                                'You must stay grounded in provided campaign evidence and preserve source IDs.'
                            ),
                        },
                        {'role': 'user', 'content': _campaign_synthesis_prompt(synthesis, deterministic_dossier)},
                    ],
                    'temperature': 0.2,
                    'max_tokens': settings.local_llm_report_max_tokens,
                },
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get('choices') if isinstance(data, dict) else None
            message = choices[0].get('message') if choices and isinstance(choices[0], dict) else None
            content = message.get('content') if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                return {
                    'enabled': True,
                    'used': False,
                    'model': model,
                    'message': 'Local LLM campaign synthesis returned no content.',
                }
            dossier = content.strip() + '\n'
            validation = validate_synthesized_campaign_dossier(dossier, synthesis)
            if not validation['ok']:
                return {
                    'enabled': True,
                    'used': False,
                    'model': model,
                    'message': 'Local LLM campaign synthesis rejected by validation.',
                    'validation': validation,
                }
            return {
                'enabled': True,
                'used': True,
                'model': model,
                'message': 'Local LLM campaign synthesis completed.',
                'validation': validation,
                'dossier': dossier,
            }
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return {
            'enabled': True,
            'used': False,
            'message': f'Local LLM campaign synthesis unavailable: {exc}',
        }
