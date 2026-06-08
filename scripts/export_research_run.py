#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_research.runs import find_research_runs, list_research_runs, load_research_run
from web_research.runs import run_budget_summary
from web_research.profiles import get_work_profile, list_work_profiles


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, '') for field in fields})


URL_REDACTION_PATTERN = re.compile(r'https?://[^\s)\]>"]+')


def _redact_text(value: Any) -> str:
    text = str(value or '')
    return URL_REDACTION_PATTERN.sub('[redacted-url]', text)


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in {'text', 'summary', 'final_report', 'evidence', 'sources', 'links', 'manual_visit_links'}:
                redacted[key] = '[redacted]'
            elif key in {'url', 'final_url', 'requested_url'}:
                redacted[key] = '[redacted-url]'
            elif key in {'quote', 'claim'}:
                redacted[key] = _redact_text(item)
            else:
                redacted[key] = _redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _source_rows(payload: dict[str, Any], *, redact: bool = False) -> list[dict[str, Any]]:
    rows = []
    for source in payload.get('sources', []) or []:
        reliability = source.get('reliability') if isinstance(source.get('reliability'), dict) else {}
        url = source.get('final_url') or source.get('url')
        rows.append(
            {
                'source_id': source.get('source_id'),
                'title': _redact_text(source.get('title')) if redact else source.get('title'),
                'url': '[redacted-url]' if redact else url,
                'source_type': reliability.get('source_type'),
                'reliability_weight': reliability.get('reliability_weight'),
                'fetched_at': source.get('fetched_at'),
                'rendered': source.get('rendered'),
            }
        )
    return rows


def _claim_rows(payload: dict[str, Any], *, redact: bool = False) -> list[dict[str, Any]]:
    rows = []
    for claim in payload.get('claims', []) or []:
        rows.append(
            {
                'claim_id': claim.get('claim_id'),
                'claim': _redact_text(claim.get('claim')) if redact else claim.get('claim'),
                'confidence': claim.get('confidence'),
                'supporting_sources': ', '.join(str(item) for item in claim.get('supporting_sources', []) or []),
                'conflicting_sources': ', '.join(str(item) for item in claim.get('conflicting_sources', []) or []),
                'supporting_evidence_count': len(claim.get('supporting_evidence', []) or []),
            }
        )
    return rows


def _audit_payload(payload: dict[str, Any], *, redact: bool = False) -> dict[str, Any]:
    audit = {
        'research_quality': payload.get('research_quality'),
        'source_quality': payload.get('source_quality'),
        'research_coverage': payload.get('research_coverage'),
        'source_freshness': payload.get('source_freshness'),
        'citation_validation': payload.get('citation_validation'),
        'citation_audit': payload.get('citation_audit'),
        'final_answer_review': payload.get('final_answer_review'),
        'agent_loop': payload.get('agent_loop'),
        'recommended_next_searches': payload.get('recommended_next_searches'),
        'blocked_sources': payload.get('blocked_sources'),
        'manual_visit_links': payload.get('manual_visit_links'),
    }
    return _redact_payload(audit) if redact else audit


def _count_items(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return len(value) if isinstance(value, list) else 0


def _quality_summary(payload: dict[str, Any]) -> dict[str, Any]:
    research_quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    source_quality = payload.get('source_quality') if isinstance(payload.get('source_quality'), dict) else {}
    research_coverage = payload.get('research_coverage') if isinstance(payload.get('research_coverage'), dict) else {}
    source_freshness = payload.get('source_freshness') if isinstance(payload.get('source_freshness'), dict) else {}
    citation_audit = payload.get('citation_audit') if isinstance(payload.get('citation_audit'), dict) else {}
    citation_validation = payload.get('citation_validation') if isinstance(payload.get('citation_validation'), dict) else {}
    final_answer_review = payload.get('final_answer_review') if isinstance(payload.get('final_answer_review'), dict) else {}
    return {
        'research_label': research_quality.get('label'),
        'research_score': research_quality.get('score'),
        'source_label': source_quality.get('label'),
        'source_score': source_quality.get('score'),
        'source_downgrade_reasons': list(source_quality.get('downgrade_reasons', []) or []),
        'citation_audit_ok': citation_audit.get('ok'),
        'citation_validation_ok': citation_validation.get('ok'),
        'final_answer_review_ok': final_answer_review.get('ok'),
        'final_answer_review_issue_count': final_answer_review.get('issue_count'),
        'coverage_planned_intents': research_coverage.get('planned_intent_count'),
        'coverage_satisfied_intents': research_coverage.get('satisfied_intent_count'),
        'coverage_missing_intents': list(research_coverage.get('missing_intents', []) or []),
        'freshness_current_sensitive': source_freshness.get('current_sensitive'),
        'freshness_has_evidence': source_freshness.get('content_freshness_evidence'),
        'freshness_gaps': list(source_freshness.get('gaps', []) or []),
    }


def _manifest_payload(
    run_id: str,
    metadata: dict[str, Any],
    payload: dict[str, Any],
    file_map: dict[str, str],
    *,
    redacted: bool = False,
) -> dict[str, Any]:
    return {
        'schema_version': 1,
        'redacted': redacted,
        'run': {
            'run_id': run_id,
            'kind': metadata.get('kind'),
            'status': metadata.get('status'),
            'query': metadata.get('query'),
            'parent_run_id': metadata.get('parent_run_id'),
            'created_at': metadata.get('created_at'),
            'updated_at': metadata.get('updated_at'),
        },
        'counts': {
            'sources': _count_items(payload, 'sources'),
            'evidence': _count_items(payload, 'evidence'),
            'claims': _count_items(payload, 'claims'),
            'blocked_sources': _count_items(payload, 'blocked_sources'),
            'manual_visit_links': _count_items(payload, 'manual_visit_links'),
            'recommended_next_searches': _count_items(payload, 'recommended_next_searches'),
        },
        'quality': _quality_summary(payload),
        'budget': payload.get('budget') if isinstance(payload.get('budget'), dict) else run_budget_summary(payload),
        'files': file_map,
    }


def _fmt_value(value: Any) -> str:
    if value is None or value == '':
        return 'n/a'
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    return str(value)


def _join_items(items: list[Any], *, empty: str = 'none') -> str:
    values = [str(item) for item in items if str(item).strip()]
    return ', '.join(values) if values else empty


def _index_markdown(manifest: dict[str, Any]) -> str:
    run = manifest.get('run') if isinstance(manifest.get('run'), dict) else {}
    counts = manifest.get('counts') if isinstance(manifest.get('counts'), dict) else {}
    quality = manifest.get('quality') if isinstance(manifest.get('quality'), dict) else {}
    budget = manifest.get('budget') if isinstance(manifest.get('budget'), dict) else {}
    files = manifest.get('files') if isinstance(manifest.get('files'), dict) else {}
    lines = [
        f"# Research Export: {_fmt_value(run.get('run_id'))}",
        '',
        f"- Query: {_fmt_value(run.get('query'))}",
        f"- Kind: {_fmt_value(run.get('kind'))}",
        f"- Status: {_fmt_value(run.get('status'))}",
        f"- Created: {_fmt_value(run.get('created_at'))}",
        f"- Updated: {_fmt_value(run.get('updated_at'))}",
        f"- Parent run: {_fmt_value(run.get('parent_run_id'))}",
        f"- Redacted export: {_fmt_value(manifest.get('redacted'))}",
        '',
        '## Counts',
        '',
        f"- Sources: {_fmt_value(counts.get('sources'))}",
        f"- Evidence snippets: {_fmt_value(counts.get('evidence'))}",
        f"- Claims: {_fmt_value(counts.get('claims'))}",
        f"- Blocked sources: {_fmt_value(counts.get('blocked_sources'))}",
        f"- Manual visit links: {_fmt_value(counts.get('manual_visit_links'))}",
        f"- Recommended next searches: {_fmt_value(counts.get('recommended_next_searches'))}",
        f"- Rendered sources: {_fmt_value(budget.get('rendered_source_count'))}",
        f"- Follow-up searches: {_fmt_value(budget.get('follow_up_search_count'))}",
        f"- Blocked sources: {_fmt_value(budget.get('blocked_source_count'))}",
        '',
        '## Quality',
        '',
        f"- Research quality: {_fmt_value(quality.get('research_label'))} ({_fmt_value(quality.get('research_score'))}/100)",
        f"- Source quality: {_fmt_value(quality.get('source_label'))} ({_fmt_value(quality.get('source_score'))}/100)",
        f"- Citation audit ok: {_fmt_value(quality.get('citation_audit_ok'))}",
        f"- Citation validation ok: {_fmt_value(quality.get('citation_validation_ok'))}",
        f"- Final answer review ok: {_fmt_value(quality.get('final_answer_review_ok'))}",
        f"- Final answer review issues: {_fmt_value(quality.get('final_answer_review_issue_count'))}",
        (
            f"- Coverage: {_fmt_value(quality.get('coverage_satisfied_intents'))}/"
            f"{_fmt_value(quality.get('coverage_planned_intents'))} intents satisfied"
        ),
        f"- Missing intents: {_join_items(list(quality.get('coverage_missing_intents', []) or []))}",
        f"- Current-sensitive: {_fmt_value(quality.get('freshness_current_sensitive'))}",
        f"- Freshness evidence: {_fmt_value(quality.get('freshness_has_evidence'))}",
        f"- Freshness gaps: {_join_items(list(quality.get('freshness_gaps', []) or []))}",
        '',
        '## Files',
        '',
    ]
    for label, filename in sorted(files.items()):
        lines.append(f"- [{filename}]({filename}) - {label.replace('_', ' ')}")
    lines.append('')
    return '\n'.join(lines)


def _batch_index_markdown(batch: dict[str, Any]) -> str:
    lines = [
        '# Research Export Batch',
        '',
        f"- Selector: {_fmt_value(batch.get('selector'))}",
        f"- Requested: {_fmt_value(batch.get('requested_count'))}",
        f"- Exported: {_fmt_value(batch.get('exported_count'))}",
        f"- Failed: {_fmt_value(batch.get('failed_count'))}",
        '',
        '## Runs',
        '',
    ]
    exports = batch.get('exports') if isinstance(batch.get('exports'), list) else []
    for item in exports:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get('run_id') or '')
        if item.get('ok'):
            lines.append(f"- [{run_id}]({run_id}/index.md)")
        else:
            lines.append(f"- {run_id}: failed - {_fmt_value(item.get('message'))}")
    lines.append('')
    return '\n'.join(lines)


def select_research_run_ids(
    *,
    latest: int | None = None,
    find: str | None = None,
    limit: int = 5,
    runs_root: Path | None = None,
) -> dict[str, Any]:
    if latest is not None and find:
        return {'ok': False, 'message': 'Use either --latest or --find, not both.'}
    if latest is not None:
        selected_limit = max(1, min(latest, 100))
        listed = list_research_runs(limit=selected_limit, root=runs_root)
        if not listed.get('ok'):
            return listed
        return {
            'ok': True,
            'selector': f'latest:{selected_limit}',
            'run_ids': [str(run.get('run_id')) for run in listed.get('runs', []) or [] if run.get('run_id')],
            'runs': listed.get('runs', []) or [],
        }
    if find:
        selected_limit = max(1, min(limit, 20))
        found = find_research_runs(find, limit=selected_limit, root=runs_root)
        if not found.get('ok'):
            return found
        return {
            'ok': True,
            'selector': f'find:{find}',
            'run_ids': [str(run.get('run_id')) for run in found.get('runs', []) or [] if run.get('run_id')],
            'runs': found.get('runs', []) or [],
        }
    return {'ok': False, 'message': 'Provide a run_id, --latest, or --find.'}


def export_research_run(
    run_id: str,
    *,
    output_dir: Path,
    runs_root: Path | None = None,
    zip_bundle: bool = False,
    redact: bool = False,
) -> dict[str, Any]:
    loaded = load_research_run(run_id, root=runs_root)
    if not loaded.get('ok'):
        return loaded
    payload = loaded.get('payload') if isinstance(loaded.get('payload'), dict) else {}
    metadata = loaded.get('run') if isinstance(loaded.get('run'), dict) else {}
    bundle_dir = output_dir / run_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    report = _redact_text(payload.get('final_report')) if redact else str(payload.get('final_report') or '')
    if report:
        (bundle_dir / 'report.md').write_text(report, encoding='utf-8')
    export_payload = _redact_payload(payload) if redact else payload
    _write_json(bundle_dir / 'run.json', {'run': metadata, 'payload': export_payload, 'redacted': redact})
    _write_json(bundle_dir / 'audit.json', _audit_payload(payload, redact=redact))
    _write_csv(
        bundle_dir / 'sources.csv',
        _source_rows(payload, redact=redact),
        ['source_id', 'title', 'url', 'source_type', 'reliability_weight', 'fetched_at', 'rendered'],
    )
    _write_csv(
        bundle_dir / 'claims.csv',
        _claim_rows(payload, redact=redact),
        ['claim_id', 'claim', 'confidence', 'supporting_sources', 'conflicting_sources', 'supporting_evidence_count'],
    )
    file_map = {
        'audit': 'audit.json',
        'claims': 'claims.csv',
        'index': 'index.md',
        'manifest': 'manifest.json',
        'run': 'run.json',
        'sources': 'sources.csv',
    }
    if report:
        file_map['report'] = 'report.md'
    manifest = _manifest_payload(run_id, metadata, payload, file_map, redacted=redact)
    _write_json(bundle_dir / 'manifest.json', manifest)
    (bundle_dir / 'index.md').write_text(_index_markdown(manifest), encoding='utf-8')
    result = {
        'ok': True,
        'run_id': run_id,
        'bundle_dir': str(bundle_dir),
        'files': sorted(path.name for path in bundle_dir.iterdir() if path.is_file()),
        'redacted': redact,
    }
    if zip_bundle:
        archive_path = shutil.make_archive(str(bundle_dir), 'zip', root_dir=bundle_dir)
        result['archive_path'] = archive_path
    return result


def export_research_runs(
    run_ids: list[str],
    *,
    output_dir: Path,
    runs_root: Path | None = None,
    zip_bundle: bool = False,
    selector: str = 'explicit',
    redact: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    exports = []
    for run_id in run_ids:
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        exports.append(
            export_research_run(
                run_id,
                output_dir=output_dir,
                runs_root=runs_root,
                zip_bundle=zip_bundle,
                redact=redact,
            )
        )
    empty = not exports
    batch = {
        'ok': bool(exports) and all(item.get('ok') for item in exports),
        'selector': selector,
        'requested_count': len(seen),
        'exported_count': sum(1 for item in exports if item.get('ok')),
        'failed_count': sum(1 for item in exports if not item.get('ok')),
        'empty': empty,
        'redacted': redact,
        'exports': exports,
    }
    if empty:
        batch['message'] = 'No research runs matched the batch export selector.'
    _write_json(output_dir / 'batch_manifest.json', batch)
    (output_dir / 'index.md').write_text(_batch_index_markdown(batch), encoding='utf-8')
    batch['batch_manifest_path'] = str(output_dir / 'batch_manifest.json')
    batch['batch_index_path'] = str(output_dir / 'index.md')
    return batch


def main() -> int:
    parser = argparse.ArgumentParser(description='Export saved research runs as reports, CSV tables, audit JSON, and indexes.')
    parser.add_argument('run_id', nargs='?')
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.runtime' / 'exports')
    parser.add_argument('--runs-root', type=Path, default=None)
    parser.add_argument('--zip', action='store_true', dest='zip_bundle')
    parser.add_argument('--redact', action='store_true', help='Redact URLs and source text from exported share bundles.')
    parser.add_argument('--profile', choices=[item['name'] for item in list_work_profiles()], default=None)
    parser.add_argument('--list-profiles', action='store_true', help='Print available work profiles and exit.')
    parser.add_argument('--latest', type=int, default=None, help='Export the latest N research runs.')
    parser.add_argument('--find', type=str, default=None, help='Export runs matching this query.')
    parser.add_argument('--limit', type=int, default=5, help='Maximum runs for --find.')
    args = parser.parse_args()

    if args.list_profiles:
        print(json.dumps({'ok': True, 'profiles': list_work_profiles()}, indent=2))
        return 0

    profile = get_work_profile(args.profile) if args.profile else None
    redact = args.redact or bool(profile and profile.redact_exports)
    output_dir = args.output_dir.expanduser().resolve()
    runs_root = args.runs_root.expanduser().resolve() if args.runs_root else None
    if args.run_id and (args.latest is not None or args.find):
        result = {'ok': False, 'message': 'Use a positional run_id or a batch selector, not both.'}
    elif args.run_id:
        result = export_research_run(
            args.run_id,
            output_dir=output_dir,
            runs_root=runs_root,
            zip_bundle=args.zip_bundle,
            redact=redact,
        )
    else:
        selected = select_research_run_ids(latest=args.latest, find=args.find, limit=args.limit, runs_root=runs_root)
        if selected.get('ok'):
            result = export_research_runs(
                selected.get('run_ids', []) or [],
                output_dir=output_dir,
                runs_root=runs_root,
                zip_bundle=args.zip_bundle,
                selector=str(selected.get('selector') or 'batch'),
                redact=redact,
            )
        else:
            result = selected
    print(json.dumps(result, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
