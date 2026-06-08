from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


TOKEN_RE = re.compile(r'[a-z0-9][a-z0-9_-]{2,}')
STOPWORDS = {
    'about', 'after', 'also', 'and', 'are', 'because', 'been', 'before', 'but', 'can', 'could',
    'from', 'have', 'into', 'latest', 'more', 'not', 'one', 'only', 'research', 'source',
    'that', 'the', 'their', 'there', 'this', 'using', 'were', 'what', 'when', 'with', 'would',
}


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(str(text or '').lower()) if token not in STOPWORDS]


def _chunks(text: str, *, chunk_words: int = 90, overlap_words: int = 20) -> list[tuple[int, int, str]]:
    words = str(text or '').split()
    if not words:
        return []
    chunk_words = max(20, chunk_words)
    overlap_words = max(0, min(overlap_words, chunk_words // 2))
    chunks = []
    index = 0
    while index < len(words):
        end = min(len(words), index + chunk_words)
        chunks.append((index, end, ' '.join(words[index:end])))
        if end >= len(words):
            break
        index = max(index + 1, end - overlap_words)
    return chunks


def _score_chunk(query_tokens: set[str], chunk_text: str, *, reliability_weight: str) -> tuple[float, list[str]]:
    chunk_tokens = tokenize(chunk_text)
    counts = Counter(chunk_tokens)
    if not counts:
        return 0.0, []
    overlap = sorted(query_tokens & set(counts))
    overlap_score = sum(counts[token] for token in overlap)
    density = overlap_score / max(1, len(chunk_tokens))
    reliability_bonus = {'strong': 0.25, 'medium': 0.12, 'supporting': 0.0}.get(reliability_weight, 0.05)
    score = (len(overlap) * 0.5) + (density * 6) + reliability_bonus
    return round(score, 4), overlap


def build_evidence_index(
    query: str,
    sources: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    *,
    top_k: int = 12,
    chunk_words: int = 90,
) -> dict[str, Any]:
    query_tokens = set(tokenize(query))
    chunks = []
    source_chunk_counts: dict[int, int] = {}
    source_top_scores: dict[int, float] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = source.get('source_id')
        if source_id is None:
            continue
        try:
            source_id_int = int(source_id)
        except (TypeError, ValueError):
            continue
        reliability = source.get('reliability') if isinstance(source.get('reliability'), dict) else {}
        reliability_weight = str(reliability.get('reliability_weight') or 'unknown')
        text = str(source.get('text') or source.get('summary') or '')
        title = str(source.get('title') or source.get('final_url') or source.get('url') or f'source:{source_id_int}')
        url = str(source.get('final_url') or source.get('url') or '')
        for chunk_index, (word_start, word_end, chunk_text) in enumerate(_chunks(text, chunk_words=chunk_words), start=1):
            score, matched_terms = _score_chunk(query_tokens, chunk_text, reliability_weight=reliability_weight)
            item = {
                'chunk_id': f'source:{source_id_int}:chunk:{chunk_index}',
                'source_id': source_id_int,
                'title': title,
                'url': url,
                'word_range': [word_start, word_end],
                'score': score,
                'matched_terms': matched_terms,
                'reliability_weight': reliability_weight,
                'source_type': reliability.get('source_type'),
                'text': chunk_text,
            }
            chunks.append(item)
            source_chunk_counts[source_id_int] = source_chunk_counts.get(source_id_int, 0) + 1
            source_top_scores[source_id_int] = max(source_top_scores.get(source_id_int, 0.0), score)
    chunks.sort(key=lambda item: (float(item.get('score') or 0), item.get('chunk_id') or ''), reverse=True)
    evidence_source_ids = {
        int(item.get('source_id'))
        for item in evidence
        if isinstance(item, dict) and str(item.get('source_id') or '').isdigit()
    }
    top_chunks = chunks[: max(1, min(top_k, 50))]
    top_source_ids = {int(item['source_id']) for item in top_chunks if str(item.get('source_id') or '').isdigit()}
    return {
        'ok': bool(chunks),
        'query_terms': sorted(query_tokens),
        'chunk_count': len(chunks),
        'top_chunk_count': len(top_chunks),
        'source_chunk_counts': {str(key): value for key, value in sorted(source_chunk_counts.items())},
        'source_top_scores': {str(key): round(value, 4) for key, value in sorted(source_top_scores.items())},
        'evidence_source_ids': sorted(evidence_source_ids),
        'top_source_ids': sorted(top_source_ids),
        'coverage': {
            'source_count': len(sources),
            'chunked_source_count': len(source_chunk_counts),
            'evidence_source_count': len(evidence_source_ids),
            'top_chunk_source_count': len(top_source_ids),
            'top_chunk_sources_without_extracted_evidence': sorted(top_source_ids - evidence_source_ids),
        },
        'top_chunks': top_chunks,
        'score_stats': {
            'max': round(max((float(item.get('score') or 0) for item in chunks), default=0.0), 4),
            'mean_top': round(
                sum(float(item.get('score') or 0) for item in top_chunks) / len(top_chunks),
                4,
            )
            if top_chunks
            else 0.0,
        },
    }
