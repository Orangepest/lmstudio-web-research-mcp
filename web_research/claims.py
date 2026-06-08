from __future__ import annotations

import re
from typing import Any

from web_research.extract import clean_text


NAVIGATION_PHRASES = (
    'search ctrl k',
    'welcome to lm studio docs',
    'system requirements getting started',
    'copy markdown',
)

CLAIM_STOPWORDS = {
    'a',
    'an',
    'and',
    'are',
    'as',
    'by',
    'does',
    'for',
    'from',
    'in',
    'inside',
    'is',
    'of',
    'on',
    'or',
    'the',
    'to',
    'with',
}

CLAIM_SYNONYMS = {
    'cannot': 'not',
    'disabled': 'disable',
    'enabled': 'enable',
    'supported': 'support',
    'supports': 'support',
    'using': 'use',
    'uses': 'use',
    'via': 'through',
    'within': 'inside',
}

NEGATION_TERMS = {'not', 'no', 'never', 'without', 'cannot', 'cant', "can't", 'unsupported', 'disable', 'disabled'}


def _claim_key(text: str) -> str:
    tokens = re.findall(r'[a-z0-9]+', text.lower())
    normalized = [CLAIM_SYNONYMS.get(token, token) for token in tokens if token not in CLAIM_STOPWORDS]
    return ' '.join(normalized)


def _claim_polarity(text: str) -> str:
    tokens = {CLAIM_SYNONYMS.get(token, token) for token in re.findall(r"[a-z0-9']+", text.lower())}
    return 'negative' if tokens & NEGATION_TERMS else 'positive'


def _conflict_key(text: str) -> str:
    tokens = re.findall(r'[a-z0-9]+', text.lower())
    normalized = []
    for token in tokens:
        token = CLAIM_SYNONYMS.get(token, token)
        if token == 'unsupported':
            token = 'support'
        if token in CLAIM_STOPWORDS or token in NEGATION_TERMS:
            continue
        normalized.append(token)
    return ' '.join(sorted(set(normalized)))


def _looks_like_navigation(text: str) -> bool:
    lowered = text.lower()
    if lowered.startswith('--- title:') or (' description:' in lowered and '---' in lowered):
        return True
    if lowered.startswith('title:') or lowered.startswith('description:'):
        return True
    if any(phrase in lowered for phrase in NAVIGATION_PHRASES):
        return True
    words = re.findall(r'[A-Za-z][A-Za-z0-9_-]*', text)
    if len(words) >= 12:
        short_title_words = sum(1 for word in words if word[:1].isupper() and len(word) <= 12)
        if short_title_words / len(words) > 0.7 and not any(char in text for char in '.:;'):
            return True
    return False


def _clean_claim_text(text: str) -> str:
    cleaned = clean_text(text)
    cleaned = re.sub(r'^index:\s*\d+\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^(---\s*)+', '', cleaned).strip()
    cleaned = re.sub(r'^(#{1,6}\s*)+', '', cleaned).strip()
    cleaned = re.sub(r'\s+#{1,6}\s+', ' ', cleaned)
    cleaned = re.sub(r'^[-*]\s+', '', cleaned).strip()
    cleaned = re.sub(r'\s+---\s+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _candidate_sentences(text: str) -> list[str]:
    compact = clean_text(text)
    if not compact:
        return []
    sentences = [clean_text(part) for part in re.split(r'(?<=[.!?])\s+', compact) if clean_text(part)]
    return sentences or [compact]


def _confidence_label(support_count: int, unique_source_count: int) -> str:
    if unique_source_count >= 2:
        return 'medium'
    if support_count >= 2:
        return 'low'
    return 'low'


def _annotate_conflicts(claims: list[dict[str, Any]]) -> None:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for claim in claims:
        key = _conflict_key(str(claim.get('claim') or ''))
        if not key:
            continue
        polarity = _claim_polarity(str(claim.get('claim') or ''))
        grouped.setdefault(key, {'positive': [], 'negative': []})[polarity].append(claim)

    for group in grouped.values():
        if not group['positive'] or not group['negative']:
            continue
        for claim in group['positive']:
            conflicting_sources = {
                source_id
                for other in group['negative']
                for source_id in other.get('supporting_sources', [])
            }
            claim['conflicting_sources'] = sorted(conflicting_sources)
            claim['confidence'] = 'low'
            claim['source_quality_notes'].append('Potential conflict found in another source.')
        for claim in group['negative']:
            conflicting_sources = {
                source_id
                for other in group['positive']
                for source_id in other.get('supporting_sources', [])
            }
            claim['conflicting_sources'] = sorted(conflicting_sources)
            claim['confidence'] = 'low'
            claim['source_quality_notes'].append('Potential conflict found in another source.')


def extract_claims_from_evidence(evidence: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    claims_by_key: dict[str, dict[str, Any]] = {}
    for item in evidence:
        quote = str(item.get('quote') or item.get('text') or '')
        for sentence in _candidate_sentences(quote):
            sentence = _clean_claim_text(sentence)
            if len(sentence) < 30:
                continue
            if _looks_like_navigation(sentence):
                continue
            key = _claim_key(sentence)
            if not key:
                continue
            claim = claims_by_key.setdefault(
                key,
                {
                    'claim': sentence,
                    'supporting_evidence': [],
                    'supporting_sources': [],
                    'conflicting_sources': [],
                    'confidence': 'low',
                    'source_quality_notes': [],
                },
            )
            support = {
                'source_id': item.get('source_id'),
                'citation': item.get('citation'),
                'url': item.get('url'),
                'title': item.get('title'),
                'quote': item.get('quote') or sentence,
            }
            support_key = (support.get('source_id'), support.get('citation'), support.get('quote'))
            existing_keys = {
                (entry.get('source_id'), entry.get('citation'), entry.get('quote'))
                for entry in claim['supporting_evidence']
            }
            if support_key not in existing_keys:
                claim['supporting_evidence'].append(support)

    claims = list(claims_by_key.values())
    for index, claim in enumerate(claims, start=1):
        source_ids = sorted(
            {
                support.get('source_id')
                for support in claim['supporting_evidence']
                if support.get('source_id') is not None
            }
        )
        claim['claim_id'] = index
        claim['supporting_sources'] = source_ids
        claim['confidence'] = _confidence_label(len(claim['supporting_evidence']), len(source_ids))
        if len(source_ids) >= 2:
            claim['source_quality_notes'].append('Supported by multiple sources.')
        else:
            claim['source_quality_notes'].append('Supported by one source; verify before relying on it.')

    _annotate_conflicts(claims)
    claims.sort(key=lambda item: (-len(item['supporting_evidence']), item['claim_id']))
    for index, claim in enumerate(claims[:limit], start=1):
        claim['claim_id'] = index
    return claims[:limit]


def uncertainty_notes(*, claims: list[dict[str, Any]], failures: list[dict[str, Any]], blocked_sources: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    if not claims:
        notes.append('No claim-level statements were extracted from readable evidence.')
    if blocked_sources:
        notes.append(f'{len(blocked_sources)} source(s) were blocked or required manual access.')
    if failures and not blocked_sources:
        notes.append(f'{len(failures)} source read(s) failed.')
    if any(len(claim.get('supporting_sources', [])) < 2 for claim in claims):
        notes.append('Some claims are supported by only one source.')
    return notes


def recent_change_notes(evidence: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    markers = ('changelog', 'release', 'released', 'updated', 'latest', 'new ', 'deprecated', 'removed', 'changed')
    notes: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for item in evidence:
        quote = clean_text(str(item.get('quote') or ''))
        lowered = quote.lower()
        if not quote or not any(marker in lowered for marker in markers):
            continue
        key = (item.get('source_id'), item.get('citation'))
        if key in seen:
            continue
        seen.add(key)
        notes.append(
            {
                'source_id': item.get('source_id'),
                'citation': item.get('citation'),
                'note': quote[:420],
            }
        )
        if len(notes) >= limit:
            break
    return notes
