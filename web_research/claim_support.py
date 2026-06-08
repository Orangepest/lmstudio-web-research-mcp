from __future__ import annotations

from typing import Any

from web_research.evidence_index import tokenize


def _source_ids(values: Any) -> set[int]:
    source_ids: set[int] = set()
    for value in values or []:
        try:
            source_ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return source_ids


def _claim_text(claim: dict[str, Any]) -> str:
    return str(claim.get('claim') or claim.get('text') or '').strip()


def _excerpt(text: str, *, limit: int = 180) -> str:
    normalized = ' '.join(str(text or '').split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + '...'


def build_claim_support_table(
    claims: list[dict[str, Any]],
    evidence_index: dict[str, Any],
    *,
    limit_claims: int = 12,
    limit_chunks_per_claim: int = 3,
) -> dict[str, Any]:
    """Map extracted claims to the strongest matching indexed evidence chunks."""
    top_chunks = evidence_index.get('top_chunks') if isinstance(evidence_index.get('top_chunks'), list) else []
    if not claims:
        return {
            'ok': False,
            'claim_count': 0,
            'supported_claim_count': 0,
            'unsupported_claim_count': 0,
            'multi_source_supported_claim_count': 0,
            'claims': [],
            'gaps': ['No claims were available for support mapping.'],
        }

    rows: list[dict[str, Any]] = []
    for index, claim in enumerate(claims[: max(1, limit_claims)], start=1):
        if not isinstance(claim, dict):
            continue
        text = _claim_text(claim)
        claim_tokens = set(tokenize(text))
        expected_sources = _source_ids(claim.get('supporting_sources'))
        candidates = []
        for chunk in top_chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_tokens = set(tokenize(str(chunk.get('text') or '')))
            overlap = sorted(claim_tokens & chunk_tokens)
            if not overlap:
                continue
            source_id = None
            try:
                source_id = int(chunk.get('source_id'))
            except (TypeError, ValueError):
                pass
            source_bonus = 2.0 if source_id in expected_sources else 0.0
            chunk_score = float(chunk.get('score') or 0.0)
            match_score = (len(overlap) * 1.5) + source_bonus + min(chunk_score, 5.0)
            candidates.append(
                {
                    'chunk_id': chunk.get('chunk_id'),
                    'source_id': source_id,
                    'title': chunk.get('title'),
                    'url': chunk.get('url'),
                    'score': round(match_score, 4),
                    'matched_terms': overlap[:8],
                    'excerpt': _excerpt(str(chunk.get('text') or '')),
                }
            )
        candidates.sort(
            key=lambda item: (
                float(item.get('score') or 0.0),
                1 if item.get('source_id') in expected_sources else 0,
                str(item.get('chunk_id') or ''),
            ),
            reverse=True,
        )
        support_chunks = candidates[: max(1, limit_chunks_per_claim)]
        indexed_source_ids = sorted(
            {
                int(item['source_id'])
                for item in support_chunks
                if isinstance(item.get('source_id'), int)
            }
        )
        rows.append(
            {
                'claim_id': claim.get('claim_id') or index,
                'claim': text,
                'confidence': claim.get('confidence') or 'unknown',
                'supporting_sources': sorted(expected_sources),
                'indexed_support_sources': indexed_source_ids,
                'indexed_support_count': len(support_chunks),
                'multi_source_indexed_support': len(indexed_source_ids) >= 2,
                'support_chunks': support_chunks,
                'status': 'supported' if support_chunks else 'needs_indexed_support',
            }
        )

    supported_count = sum(1 for row in rows if row['indexed_support_count'])
    unsupported_count = len(rows) - supported_count
    multi_source_count = sum(1 for row in rows if row.get('multi_source_indexed_support'))
    gaps = []
    if unsupported_count:
        gaps.append(f'{unsupported_count} claim(s) did not match any top indexed evidence chunk.')
    if supported_count and not multi_source_count:
        gaps.append('No claim has indexed support from multiple sources.')
    return {
        'ok': bool(supported_count),
        'claim_count': len(rows),
        'supported_claim_count': supported_count,
        'unsupported_claim_count': unsupported_count,
        'multi_source_supported_claim_count': multi_source_count,
        'claims': rows,
        'gaps': gaps,
    }
