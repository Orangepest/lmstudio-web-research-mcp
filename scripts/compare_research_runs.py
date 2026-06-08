#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_research.config import settings
from web_research.runs import load_research_run


def _json_default(value: object) -> str:
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


def _load_all_run_summaries(*, runs_root: Path | None = None) -> dict[str, Any]:
    base = runs_root or settings.research_runs_dir
    if not base.exists():
        return {'ok': True, 'runs': [], 'count': 0, 'total_count': 0}
    runs = []
    seen_run_ids: set[str] = set()
    for summary_path in base.glob('*/summary.json'):
        try:
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(summary, dict):
            run_id = summary.get('run_id')
            if run_id:
                seen_run_ids.add(str(run_id))
                runs.append(summary)
    for run_path in base.glob('*/run.json'):
        run_id = run_path.parent.name
        if run_id in seen_run_ids:
            continue
        loaded = load_research_run(run_id, root=base)
        if not loaded.get('ok'):
            continue
        runs.append(_run_metrics(loaded))
    runs.sort(key=lambda item: str(item.get('created_at') or ''), reverse=True)
    return {'ok': True, 'runs': runs, 'count': len(runs), 'total_count': len(runs)}


def _source_url(source: dict[str, Any]) -> str:
    return str(source.get('final_url') or source.get('url') or '')


def _source_key(source: dict[str, Any]) -> str:
    url = _source_url(source)
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower().removeprefix('www.')
    path = parsed.path.rstrip('/') or '/'
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    if not host:
        return url
    return f'{host}{path}?{query}' if query else f'{host}{path}'


def _domain(source: dict[str, Any]) -> str:
    return (urlparse(_source_url(source)).hostname or '').lower().removeprefix('www.')


def _score(payload: dict[str, Any]) -> int | None:
    quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    if quality.get('score') is None:
        return None
    return int(quality.get('score') or 0)


def _missing_intents(payload: dict[str, Any]) -> list[str]:
    coverage = payload.get('research_coverage') if isinstance(payload.get('research_coverage'), dict) else {}
    return [str(item) for item in coverage.get('missing_intents', []) or []]


def _freshness_gaps(payload: dict[str, Any]) -> list[str]:
    freshness = payload.get('source_freshness') if isinstance(payload.get('source_freshness'), dict) else {}
    return [str(item) for item in freshness.get('gaps', []) or []]


def _run_metrics(loaded: dict[str, Any]) -> dict[str, Any]:
    payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
    metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
    sources = payload.get('sources', []) if isinstance(payload.get('sources'), list) else []
    domains = sorted({domain for domain in (_domain(source) for source in sources) if domain})
    return {
        'run_id': metadata.get('run_id'),
        'kind': metadata.get('kind'),
        'status': metadata.get('status'),
        'query': metadata.get('query'),
        'parent_run_id': metadata.get('parent_run_id'),
        'created_at': metadata.get('created_at'),
        'source_count': len(sources),
        'domain_count': len(domains),
        'domains': domains,
        'claim_count': len(payload.get('claims', []) or []),
        'evidence_count': len(payload.get('evidence', []) or []),
        'research_score': _score(payload),
        'missing_intents': _missing_intents(payload),
        'freshness_gaps': _freshness_gaps(payload),
    }


def compare_research_runs(base_run_id: str, compare_run_id: str, *, runs_root: Path | None = None) -> dict[str, Any]:
    base = load_research_run(base_run_id, root=runs_root)
    if not base.get('ok'):
        return base
    compare = load_research_run(compare_run_id, root=runs_root)
    if not compare.get('ok'):
        return compare
    base_payload = base.get('payload') if isinstance(base.get('payload'), dict) else {}
    compare_payload = compare.get('payload') if isinstance(compare.get('payload'), dict) else {}
    base_sources = base_payload.get('sources', []) if isinstance(base_payload.get('sources'), list) else []
    compare_sources = compare_payload.get('sources', []) if isinstance(compare_payload.get('sources'), list) else []
    base_source_keys = {_source_key(source) for source in base_sources if _source_key(source)}
    compare_source_keys = {_source_key(source) for source in compare_sources if _source_key(source)}
    base_domains = {_domain(source) for source in base_sources if _domain(source)}
    compare_domains = {_domain(source) for source in compare_sources if _domain(source)}
    base_metrics = _run_metrics(base)
    compare_metrics = _run_metrics(compare)
    base_score = base_metrics.get('research_score')
    compare_score = compare_metrics.get('research_score')
    return {
        'ok': True,
        'base': base_metrics,
        'compare': compare_metrics,
        'delta': {
            'sources': int(compare_metrics['source_count']) - int(base_metrics['source_count']),
            'domains': int(compare_metrics['domain_count']) - int(base_metrics['domain_count']),
            'claims': int(compare_metrics['claim_count']) - int(base_metrics['claim_count']),
            'evidence': int(compare_metrics['evidence_count']) - int(base_metrics['evidence_count']),
            'research_score': None if base_score is None or compare_score is None else int(compare_score) - int(base_score),
        },
        'new_source_keys': sorted(compare_source_keys - base_source_keys),
        'removed_source_keys': sorted(base_source_keys - compare_source_keys),
        'new_domains': sorted(compare_domains - base_domains),
        'removed_domains': sorted(base_domains - compare_domains),
        'resolved_missing_intents': sorted(set(base_metrics['missing_intents']) - set(compare_metrics['missing_intents'])),
        'new_missing_intents': sorted(set(compare_metrics['missing_intents']) - set(base_metrics['missing_intents'])),
        'resolved_freshness_gaps': sorted(set(base_metrics['freshness_gaps']) - set(compare_metrics['freshness_gaps'])),
        'new_freshness_gaps': sorted(set(compare_metrics['freshness_gaps']) - set(base_metrics['freshness_gaps'])),
    }


def compare_research_chain(run_id: str, *, runs_root: Path | None = None) -> dict[str, Any]:
    listed = _load_all_run_summaries(runs_root=runs_root)
    if not listed.get('ok'):
        return listed
    summaries = [item for item in listed.get('runs', []) or [] if isinstance(item, dict)]
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for summary in summaries:
        parent_id = summary.get('parent_run_id')
        if parent_id:
            children_by_parent.setdefault(str(parent_id), []).append(summary)
    for children in children_by_parent.values():
        children.sort(key=lambda item: str(item.get('created_at') or ''), reverse=True)

    chain = []
    current_id: str | None = run_id
    seen: set[str] = set()
    while current_id:
        if current_id in seen:
            return {'ok': False, 'message': f'Cycle detected in research run chain at {current_id}', 'run_id': run_id}
        seen.add(current_id)
        loaded = load_research_run(current_id, root=runs_root)
        if not loaded.get('ok'):
            return loaded
        chain.append(_run_metrics(loaded))
        metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
        current_id = metadata.get('parent_run_id')
    chain.reverse()

    while chain and chain[-1].get('run_id') in children_by_parent:
        current_id = str(chain[-1]['run_id'])
        child = children_by_parent[current_id][0]
        child_id = str(child.get('run_id') or '')
        if not child_id:
            break
        if any(item.get('run_id') == child_id for item in chain):
            return {'ok': False, 'message': f'Cycle detected in research run chain at {child_id}', 'run_id': run_id}
        loaded = load_research_run(child_id, root=runs_root)
        if not loaded.get('ok'):
            return loaded
        chain.append(_run_metrics(loaded))

    comparisons = [
        compare_research_runs(str(chain[index]['run_id']), str(chain[index + 1]['run_id']), runs_root=runs_root)
        for index in range(len(chain) - 1)
    ]
    return {'ok': True, 'run_id': run_id, 'chain': chain, 'comparison_count': len(comparisons), 'comparisons': comparisons}


def comparison_markdown(result: dict[str, Any]) -> str:
    if not result.get('ok'):
        return f"# Research Run Comparison\n\n- Error: {result.get('message', 'unknown error')}\n"
    if 'chain' in result:
        lines = ['# Research Run Chain', '']
        for item in result.get('chain', []) or []:
            lines.append(
                f"- {item.get('run_id')}: sources={item.get('source_count')} domains={item.get('domain_count')} "
                f"claims={item.get('claim_count')} score={item.get('research_score')}"
            )
        lines.extend(['', '## Deltas', ''])
        for comparison in result.get('comparisons', []) or []:
            base = comparison.get('base', {})
            compare = comparison.get('compare', {})
            delta = comparison.get('delta', {})
            lines.append(f"- {base.get('run_id')} -> {compare.get('run_id')}: {delta}")
        return '\n'.join(lines) + '\n'
    base = result.get('base', {})
    compare = result.get('compare', {})
    delta = result.get('delta', {})
    lines = [
        '# Research Run Comparison',
        '',
        f"- Base: {base.get('run_id')}",
        f"- Compare: {compare.get('run_id')}",
        f"- Source delta: {delta.get('sources')}",
        f"- Domain delta: {delta.get('domains')}",
        f"- Claim delta: {delta.get('claims')}",
        f"- Research score delta: {delta.get('research_score')}",
        f"- New domains: {', '.join(result.get('new_domains', []) or []) or 'none'}",
        f"- Resolved missing intents: {', '.join(result.get('resolved_missing_intents', []) or []) or 'none'}",
        f"- New missing intents: {', '.join(result.get('new_missing_intents', []) or []) or 'none'}",
    ]
    return '\n'.join(lines) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description='Compare saved research runs or a parent/follow-up chain.')
    parser.add_argument('run_id', nargs='?', help='Run id to compare as a parent chain.')
    parser.add_argument('--base', type=str, default=None, help='Base run id for pairwise comparison.')
    parser.add_argument('--compare', type=str, default=None, help='Comparison run id for pairwise comparison.')
    parser.add_argument('--runs-root', type=Path, default=None)
    parser.add_argument('--output-dir', type=Path, default=None)
    args = parser.parse_args()

    runs_root = args.runs_root.expanduser().resolve() if args.runs_root else None
    if args.base or args.compare:
        if not args.base or not args.compare:
            result = {'ok': False, 'message': 'Provide both --base and --compare.'}
        else:
            result = compare_research_runs(args.base, args.compare, runs_root=runs_root)
    elif args.run_id:
        result = compare_research_chain(args.run_id, runs_root=runs_root)
    else:
        result = {'ok': False, 'message': 'Provide run_id for chain comparison or --base/--compare.'}

    if args.output_dir:
        output_dir = args.output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_json(output_dir / 'comparison.json', result)
        (output_dir / 'comparison.md').write_text(comparison_markdown(result), encoding='utf-8')
    print(json.dumps(result, indent=2, default=_json_default))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
