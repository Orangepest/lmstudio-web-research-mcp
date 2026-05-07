from __future__ import annotations

import re
from typing import Any

from web_research.extract import clean_text


def query_terms(query: str | None) -> set[str]:
    if not query:
        return set()
    return {
        token.lower()
        for token in re.findall(r'[A-Za-z0-9][A-Za-z0-9_-]{1,}', query)
        if len(token) > 1
    }


def extract_evidence(
    text: str,
    query: str | None,
    *,
    source_id: int,
    url: str,
    title: str | None,
    limit: int = 5,
    max_chars: int = 650,
) -> list[dict[str, Any]]:
    blocks = [part.strip() for part in re.split(r'\n{1,}', text) if part.strip()]
    if not blocks and text.strip():
        blocks = [text.strip()]
    terms = query_terms(query)
    scored: list[tuple[int, int, int, str]] = []
    cursor = 0
    for index, block in enumerate(blocks):
        block = clean_text(block)
        start = text.find(block, cursor)
        if start < 0:
            start = cursor
        cursor = start + len(block)
        lowered = block.lower()
        distinct_hits = sum(1 for term in terms if term in lowered)
        total_hits = sum(lowered.count(term) for term in terms)
        score = distinct_hits * 10 + total_hits
        if not terms:
            score = max(1, 5 - index)
        if score > 0:
            scored.append((score, -index, start, block))
    if not scored:
        scored = [(1, -index, text.find(block), clean_text(block)) for index, block in enumerate(blocks[:limit])]
    scored.sort(reverse=True)
    evidence: list[dict[str, Any]] = []
    for rank, (_score, _negative_index, start, block) in enumerate(scored[:limit], start=1):
        quote = block[:max_chars].rstrip()
        end = start + len(quote)
        citation = f'source:{source_id}[{start}:{end}]'
        evidence.append(
            {
                'source_id': source_id,
                'url': url,
                'title': title or 'Untitled',
                'quote': quote,
                'char_range': [start, end],
                'citation': citation,
                'rank': rank,
            }
        )
    return evidence

