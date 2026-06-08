from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


CURRENT_TERMS = (
    'latest',
    'current',
    'currently',
    'today',
    'recent',
    'newest',
    'updated',
    'changelog',
    'release',
    '2026',
)

RECENT_MARKERS = (
    'latest',
    'updated',
    'released',
    'release',
    'changelog',
    'deprecated',
    'new ',
    'changed',
)

YEAR_RE = re.compile(r'\b(20[1-3]\d)\b')


def is_current_sensitive(question: str | None) -> bool:
    lowered = str(question or '').lower()
    return any(term in lowered for term in CURRENT_TERMS)


def _years(text: str) -> list[int]:
    return sorted({int(match.group(1)) for match in YEAR_RE.finditer(text)})


def _source_text(source: dict[str, Any]) -> str:
    return ' '.join(
        str(source.get(key) or '')
        for key in ('title', 'url', 'final_url', 'summary', 'text')
    )


def build_freshness_summary(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    question = str(payload.get('question') or payload.get('query') or '')
    sources = [source for source in payload.get('sources', []) or [] if isinstance(source, dict)]
    evidence = [item for item in payload.get('evidence', []) or [] if isinstance(item, dict)]
    current_sensitive = is_current_sensitive(question)

    years = []
    marker_sources = []
    access_timestamps = []
    for source in sources:
        text = _source_text(source)
        years.extend(_years(text))
        lowered = text.lower()
        if any(marker in lowered for marker in RECENT_MARKERS):
            marker_sources.append(source.get('source_id'))
        if source.get('fetched_at'):
            access_timestamps.append(source.get('fetched_at'))
    for item in evidence:
        years.extend(_years(str(item.get('quote') or item.get('text') or '')))

    newest_year = max(years) if years else None
    recent_change_count = len(payload.get('recent_changes', []) or [])
    has_content_freshness = bool(marker_sources or recent_change_count or (newest_year and newest_year >= now.year - 1))
    gaps = []
    if current_sensitive and not has_content_freshness:
        gaps.append('Question appears current-sensitive, but selected evidence has no clear recent date or update marker.')
    if current_sensitive and not recent_change_count:
        gaps.append('No recent-change evidence snippets were extracted.')
    if sources and len(access_timestamps) < len(sources):
        gaps.append('Some sources lack fetched_at access timestamps.')

    return {
        'current_sensitive': current_sensitive,
        'newest_mentioned_year': newest_year,
        'content_freshness_evidence': has_content_freshness,
        'recent_change_count': recent_change_count,
        'sources_with_recent_markers': [item for item in marker_sources if item is not None],
        'access_timestamp_count': len(access_timestamps),
        'source_count': len(sources),
        'gaps': gaps,
    }
