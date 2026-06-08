#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.export_research_run import _redact_text, select_research_run_ids
from web_research.runs import load_research_run
from web_research.profiles import get_work_profile, list_work_profiles


DEFAULT_OUTPUT_ROOT = ROOT / '.runtime' / 'source_packs'


def _json_default(value: object) -> str:
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False, default=_json_default) + '\n' for row in rows),
        encoding='utf-8',
    )


def _source_key(source: dict[str, Any]) -> str:
    return str(source.get('final_url') or source.get('url') or source.get('source_id') or '')


def collect_source_pack(run_ids: list[str], *, runs_root: Path | None = None, redact: bool = False) -> dict[str, Any]:
    sources_by_key: dict[str, dict[str, Any]] = {}
    claims: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    included_runs = []
    failures = []
    for run_id in run_ids:
        loaded = load_research_run(run_id, root=runs_root)
        if not loaded.get('ok'):
            failures.append({'run_id': run_id, 'message': loaded.get('message')})
            continue
        payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
        metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
        included_runs.append(
            {
                'run_id': run_id,
                'kind': metadata.get('kind'),
                'query': metadata.get('query'),
                'status': metadata.get('status'),
            }
        )
        for source in payload.get('sources', []) or []:
            if not isinstance(source, dict):
                continue
            key = _source_key(source)
            if not key:
                continue
            reliability = source.get('reliability') if isinstance(source.get('reliability'), dict) else {}
            row = sources_by_key.setdefault(
                key,
                {
                    'source_id': len(sources_by_key) + 1,
                    'original_source_ids': [],
                    'run_ids': [],
                    'title': _redact_text(source.get('title')) if redact else source.get('title'),
                    'url': '[redacted-url]' if redact else source.get('final_url') or source.get('url'),
                    'source_type': reliability.get('source_type'),
                    'reliability_weight': reliability.get('reliability_weight'),
                    'rendered': bool(source.get('rendered')),
                },
            )
            row['run_ids'] = sorted(set(row.get('run_ids', [])) | {run_id})
            if source.get('source_id') is not None:
                row['original_source_ids'] = sorted(set(row.get('original_source_ids', [])) | {source.get('source_id')})
        for claim in payload.get('claims', []) or []:
            if isinstance(claim, dict):
                claims.append(
                    {
                        'run_id': run_id,
                        'claim_id': claim.get('claim_id'),
                        'claim': _redact_text(claim.get('claim')) if redact else claim.get('claim'),
                        'confidence': claim.get('confidence'),
                        'supporting_sources': claim.get('supporting_sources', []) or [],
                        'conflicting_sources': claim.get('conflicting_sources', []) or [],
                    }
                )
        for item in payload.get('evidence', []) or []:
            if isinstance(item, dict):
                evidence.append(
                    {
                        'run_id': run_id,
                        'source_id': item.get('source_id'),
                        'citation': item.get('citation'),
                        'title': _redact_text(item.get('title')) if redact else item.get('title'),
                        'url': '[redacted-url]' if redact else item.get('url'),
                        'quote': _redact_text(item.get('quote')) if redact else item.get('quote'),
                    }
                )
    sources = sorted(sources_by_key.values(), key=lambda item: int(item.get('source_id') or 0))
    return {
        'ok': bool(included_runs),
        'redacted': redact,
        'runs': included_runs,
        'failures': failures,
        'counts': {
            'runs': len(included_runs),
            'sources': len(sources),
            'claims': len(claims),
            'evidence': len(evidence),
            'failures': len(failures),
        },
        'sources': sources,
        'claims': claims,
        'evidence': evidence,
    }


def source_pack_markdown(pack: dict[str, Any]) -> str:
    counts = pack.get('counts') if isinstance(pack.get('counts'), dict) else {}
    lines = [
        '# Offline Source Pack',
        '',
        f"- Redacted: {'yes' if pack.get('redacted') else 'no'}",
        f"- Runs: {counts.get('runs', 0)}",
        f"- Sources: {counts.get('sources', 0)}",
        f"- Claims: {counts.get('claims', 0)}",
        f"- Evidence snippets: {counts.get('evidence', 0)}",
        '',
        '## Runs',
        '',
    ]
    for run in pack.get('runs', []) or []:
        if isinstance(run, dict):
            lines.append(f"- {run.get('run_id')}: {run.get('query')}")
    lines.extend(['', '## Sources', ''])
    for source in pack.get('sources', [])[:50]:
        if isinstance(source, dict):
            lines.append(
                f"- source:{source.get('source_id')} {source.get('title') or source.get('url')} "
                f"({source.get('source_type') or 'unknown'}, {source.get('reliability_weight') or 'unknown'})"
            )
    lines.append('')
    return '\n'.join(lines)


def write_source_pack(pack: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / 'manifest.json', {key: value for key, value in pack.items() if key not in {'sources', 'claims', 'evidence'}})
    _write_jsonl(output_dir / 'sources.jsonl', pack.get('sources', []) or [])
    _write_jsonl(output_dir / 'claims.jsonl', pack.get('claims', []) or [])
    _write_jsonl(output_dir / 'evidence.jsonl', pack.get('evidence', []) or [])
    (output_dir / 'index.md').write_text(source_pack_markdown(pack), encoding='utf-8')
    return {
        'ok': bool(pack.get('ok')),
        'output_dir': str(output_dir),
        'files': ['claims.jsonl', 'evidence.jsonl', 'index.md', 'manifest.json', 'sources.jsonl'],
        'counts': pack.get('counts'),
        'redacted': bool(pack.get('redacted')),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Build an offline source pack from saved research runs.')
    parser.add_argument('run_id', nargs='*')
    parser.add_argument('--latest', type=int, default=None)
    parser.add_argument('--find', type=str, default=None)
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--runs-root', type=Path, default=None)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument('--redact', action='store_true')
    parser.add_argument('--profile', choices=[item['name'] for item in list_work_profiles()], default=None)
    parser.add_argument('--list-profiles', action='store_true')
    args = parser.parse_args()
    if args.list_profiles:
        print(json.dumps({'profiles': list_work_profiles()}, indent=2))
        return 0
    profile = get_work_profile(args.profile) if args.profile else None
    redact = args.redact or bool(profile and profile.redact_exports)

    runs_root = args.runs_root.expanduser().resolve() if args.runs_root else None
    if args.run_id and (args.latest is not None or args.find):
        result = {'ok': False, 'message': 'Use explicit run IDs or a selector, not both.'}
    elif args.run_id:
        run_ids = args.run_id
        pack = collect_source_pack(run_ids, runs_root=runs_root, redact=redact)
        result = write_source_pack(pack, args.output_dir.expanduser().resolve())
    else:
        latest = args.latest
        if latest is None and profile and not args.find:
            latest = profile.source_pack_latest
        selected = select_research_run_ids(latest=latest, find=args.find, limit=args.limit, runs_root=runs_root)
        if not selected.get('ok'):
            result = selected
        else:
            pack = collect_source_pack(selected.get('run_ids', []) or [], runs_root=runs_root, redact=redact)
            result = write_source_pack(pack, args.output_dir.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
