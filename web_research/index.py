from __future__ import annotations

import json
import math
import re
import hashlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from web_research.config import settings
from web_research.runs import load_research_run


TOKEN_RE = re.compile(r'[a-z0-9][a-z0-9_-]{2,}')
STOPWORDS = {
    'about', 'after', 'also', 'and', 'are', 'because', 'been', 'before', 'but', 'can', 'could',
    'from', 'have', 'into', 'latest', 'local', 'more', 'not', 'one', 'only', 'research', 'source',
    'that', 'the', 'their', 'there', 'this', 'using', 'were', 'what', 'when', 'with', 'would',
}


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall((text or '').lower()) if token not in STOPWORDS]


def _vector(text: str) -> dict[str, float]:
    counts = Counter(tokenize(text))
    total = sum(counts.values()) or 1
    return {token: count / total for token, count in counts.items()}


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    dot = sum(left[token] * right[token] for token in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _all_run_ids(runs_root: Path) -> list[str]:
    if not runs_root.exists():
        return []
    return sorted({path.parent.name for path in runs_root.glob('*/run.json')})


def _truncate(text: str, limit: int = 500) -> str:
    text = ' '.join((text or '').split())
    return text[:limit].rstrip()


def _entry(
    *,
    run_id: str,
    kind: str,
    item_type: str,
    title: str,
    text: str,
    url: str | None = None,
    source_id: int | None = None,
    claim_id: int | None = None,
) -> dict[str, Any]:
    fallback_id = hashlib.sha256(f'{title}\0{text}'.encode('utf-8', errors='ignore')).hexdigest()[:12]
    return {
        'entry_id': ':'.join(str(item) for item in (run_id, item_type, source_id or claim_id or fallback_id)),
        'run_id': run_id,
        'kind': kind,
        'item_type': item_type,
        'title': title,
        'url': url,
        'source_id': source_id,
        'claim_id': claim_id,
        'text_excerpt': _truncate(text),
        'tokens': sorted(set(tokenize(f'{title} {text}'))),
        'vector': _vector(f'{title} {text}'),
    }


def _entries_for_run(loaded: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
    payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
    run_id = str(metadata.get('run_id') or payload.get('run_id') or '')
    kind = str(metadata.get('kind') or '')
    entries: list[dict[str, Any]] = []
    question = str(payload.get('question') or payload.get('query') or metadata.get('query') or '')
    report = str(payload.get('final_report') or '')
    if report:
        entries.append(_entry(run_id=run_id, kind=kind, item_type='report', title=question or 'Final report', text=report))
    for source in payload.get('sources', []) or []:
        if not isinstance(source, dict):
            continue
        text = str(source.get('text') or source.get('summary') or '')
        if not text:
            continue
        entries.append(
            _entry(
                run_id=run_id,
                kind=kind,
                item_type='source',
                title=str(source.get('title') or source.get('final_url') or source.get('url') or 'Source'),
                text=text,
                url=str(source.get('final_url') or source.get('url') or ''),
                source_id=source.get('source_id'),
            )
        )
    for claim in payload.get('claims', []) or []:
        if not isinstance(claim, dict) or not claim.get('claim'):
            continue
        entries.append(
            _entry(
                run_id=run_id,
                kind=kind,
                item_type='claim',
                title=question or 'Claim',
                text=str(claim.get('claim')),
                claim_id=claim.get('claim_id'),
            )
        )
    return entries


def build_research_index(*, runs_root: Path | None = None) -> dict[str, Any]:
    root = runs_root or settings.research_runs_dir
    entries: list[dict[str, Any]] = []
    for run_id in _all_run_ids(root):
        loaded = load_research_run(run_id, root=root)
        if loaded.get('ok'):
            entries.extend(_entries_for_run(loaded))
    return {
        'ok': True,
        'schema_version': 1,
        'built_at': _utc_now(),
        'runs_root': str(root),
        'run_count': len(_all_run_ids(root)),
        'entry_count': len(entries),
        'entries': entries,
    }


def write_research_index(index: dict[str, Any], path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        **index,
        'entries': [
            {key: value for key, value in entry.items() if key != 'vector'}
            | {'vector': entry.get('vector', {})}
            for entry in index.get('entries', []) or []
        ],
    }
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'ok': True, 'path': str(path), 'entry_count': len(index.get('entries', []) or [])}


def load_research_index(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def search_research_index(index: dict[str, Any], query: str, *, limit: int = 10) -> dict[str, Any]:
    query_vector = _vector(query)
    query_tokens = set(tokenize(query))
    matches = []
    for entry in index.get('entries', []) or []:
        if not isinstance(entry, dict):
            continue
        vector = entry.get('vector') if isinstance(entry.get('vector'), dict) else {}
        score = _cosine(query_vector, {str(key): float(value) for key, value in vector.items()})
        token_overlap = sorted(query_tokens & set(entry.get('tokens', []) or []))
        if token_overlap:
            score += min(0.25, len(token_overlap) * 0.03)
        if score <= 0:
            continue
        matches.append(
            {
                'score': round(score, 4),
                'entry_id': entry.get('entry_id'),
                'run_id': entry.get('run_id'),
                'kind': entry.get('kind'),
                'item_type': entry.get('item_type'),
                'title': entry.get('title'),
                'url': entry.get('url'),
                'source_id': entry.get('source_id'),
                'claim_id': entry.get('claim_id'),
                'matched_terms': token_overlap,
                'text_excerpt': entry.get('text_excerpt'),
            }
        )
    matches.sort(key=lambda item: item['score'], reverse=True)
    return {'ok': True, 'query': query, 'count': len(matches[:limit]), 'matches': matches[: max(1, min(limit, 50))]}
