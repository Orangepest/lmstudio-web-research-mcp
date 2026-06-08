from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from web_research.campaigns import load_research_campaign, summarize_campaign
from web_research.jobs import utc_now
from web_research.local_llm import synthesize_campaign_dossier
from web_research.runs import load_research_run, run_budget_summary


URL_REDACTION_PATTERN = re.compile(r'https?://[^\s)\]>"]+')


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, '') for field in fields})


def _redact_text(value: Any) -> str:
    return URL_REDACTION_PATTERN.sub('[redacted-url]', str(value or ''))


def _source_key(source: dict[str, Any]) -> str:
    url = str(source.get('final_url') or source.get('url') or '').strip().lower()
    if url:
        return f'url:{url}'
    title = str(source.get('title') or '').strip().lower()
    if title:
        return f'title:{title}'
    return f"source:{source.get('source_id')}"


def _source_url(source: dict[str, Any], *, redact: bool) -> str:
    url = str(source.get('final_url') or source.get('url') or '')
    return '[redacted-url]' if redact and url else url


def _quality_score(payload: dict[str, Any]) -> Any:
    quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    return quality.get('score')


def _quality_label(payload: dict[str, Any]) -> Any:
    quality = payload.get('research_quality') if isinstance(payload.get('research_quality'), dict) else {}
    return quality.get('label')


def _collect_campaign_runs(
    campaign_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
) -> dict[str, Any]:
    loaded = load_research_campaign(campaign_root, campaign_id)
    if not loaded.get('ok'):
        return loaded
    summary = summarize_campaign(loaded['campaign'], jobs_root=jobs_root, runs_root=runs_root)
    completed_runs = []
    missing_runs = []
    for run_id in summary.get('run_ids') or []:
        run = load_research_run(str(run_id), root=runs_root)
        if not run.get('ok'):
            missing_runs.append({'run_id': run_id, 'message': run.get('message')})
            continue
        metadata = run.get('run') if isinstance(run.get('run'), dict) else {}
        payload = run.get('payload') if isinstance(run.get('payload'), dict) else {}
        if metadata.get('status') != 'completed':
            missing_runs.append({'run_id': run_id, 'status': metadata.get('status'), 'message': 'Run is not completed.'})
            continue
        completed_runs.append({'run_id': run_id, 'metadata': metadata, 'payload': payload})
    return {'ok': True, 'campaign': summary, 'runs': completed_runs, 'missing_runs': missing_runs}


def build_campaign_synthesis(
    campaign_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    redact: bool = False,
) -> dict[str, Any]:
    collected = _collect_campaign_runs(campaign_id, campaign_root=campaign_root, jobs_root=jobs_root, runs_root=runs_root)
    if not collected.get('ok'):
        return collected
    campaign = collected['campaign']
    runs = collected['runs']
    source_rows: list[dict[str, Any]] = []
    claim_rows: list[dict[str, Any]] = []
    audit_runs = []
    source_by_key: dict[str, int] = {}
    source_maps: dict[str, dict[str, int]] = {}

    for run in runs:
        run_id = str(run['run_id'])
        payload = run['payload']
        source_maps[run_id] = {}
        for source in payload.get('sources') or []:
            if not isinstance(source, dict):
                continue
            key = _source_key(source)
            global_id = source_by_key.get(key)
            if global_id is None:
                reliability = source.get('reliability') if isinstance(source.get('reliability'), dict) else {}
                global_id = len(source_rows) + 1
                source_by_key[key] = global_id
                source_rows.append(
                    {
                        'campaign_source_id': global_id,
                        'first_run_id': run_id,
                        'source_id': source.get('source_id'),
                        'title': _redact_text(source.get('title')) if redact else source.get('title'),
                        'url': _source_url(source, redact=redact),
                        'source_type': reliability.get('source_type'),
                        'reliability_weight': reliability.get('reliability_weight'),
                        'fetched_at': source.get('fetched_at'),
                        'rendered': source.get('rendered'),
                    }
                )
            source_maps[run_id][str(source.get('source_id'))] = global_id

    for run in runs:
        run_id = str(run['run_id'])
        payload = run['payload']
        for claim in payload.get('claims') or []:
            if not isinstance(claim, dict):
                continue
            supporting = [
                source_maps.get(run_id, {}).get(str(source_id), source_id)
                for source_id in claim.get('supporting_sources', []) or []
            ]
            conflicting = [
                source_maps.get(run_id, {}).get(str(source_id), source_id)
                for source_id in claim.get('conflicting_sources', []) or []
            ]
            claim_rows.append(
                {
                    'campaign_claim_id': len(claim_rows) + 1,
                    'run_id': run_id,
                    'claim_id': claim.get('claim_id'),
                    'claim': _redact_text(claim.get('claim')) if redact else claim.get('claim'),
                    'confidence': claim.get('confidence'),
                    'supporting_campaign_sources': ', '.join(str(item) for item in supporting),
                    'conflicting_campaign_sources': ', '.join(str(item) for item in conflicting),
                    'supporting_evidence_count': len(claim.get('supporting_evidence', []) or []),
                }
            )
        audit_runs.append(
            {
                'run_id': run_id,
                'query': run['metadata'].get('query'),
                'status': run['metadata'].get('status'),
                'research_quality_label': _quality_label(payload),
                'research_quality_score': _quality_score(payload),
                'source_count': len(payload.get('sources', []) or []),
                'claim_count': len(payload.get('claims', []) or []),
                'budget': payload.get('budget') if isinstance(payload.get('budget'), dict) else run_budget_summary(payload),
                'final_answer_review': payload.get('final_answer_review'),
                'citation_audit': payload.get('citation_audit'),
                'contradiction_table': payload.get('contradiction_table'),
                'recommended_next_searches': payload.get('recommended_next_searches'),
            }
        )

    report = _campaign_report(campaign, runs, source_rows, claim_rows, missing_runs=collected.get('missing_runs') or [], redact=redact)
    manifest = {
        'schema_version': 1,
        'campaign_id': campaign_id,
        'objective': campaign.get('objective'),
        'campaign_status': campaign.get('status'),
        'redacted': redact,
        'created_at': utc_now(),
        'counts': {
            'campaign_steps': campaign.get('step_count'),
            'completed_runs': len(runs),
            'missing_runs': len(collected.get('missing_runs') or []),
            'deduped_sources': len(source_rows),
            'claims': len(claim_rows),
        },
        'files': {
            'audit': 'audit.json',
            'claims': 'claims.csv',
            'dossier': 'dossier.md',
            'index': 'index.md',
            'manifest': 'manifest.json',
            'sources': 'sources.csv',
        },
    }
    return {
        'ok': True,
        'campaign': campaign,
        'run_count': len(runs),
        'missing_runs': collected.get('missing_runs') or [],
        'source_count': len(source_rows),
        'claim_count': len(claim_rows),
        'sources': source_rows,
        'claims': claim_rows,
        'audit': {'campaign': campaign, 'runs': audit_runs, 'missing_runs': collected.get('missing_runs') or []},
        'manifest': manifest,
        'dossier': report,
        'deterministic_dossier': report,
        'campaign_synthesis': {'enabled': False, 'used': False, 'message': 'Local LLM campaign synthesis not requested.'},
        'redacted': redact,
    }


async def apply_campaign_narrative_synthesis(synthesis: dict[str, Any], *, enabled: bool | None = None) -> dict[str, Any]:
    if not synthesis.get('ok'):
        return synthesis
    deterministic = str(synthesis.get('deterministic_dossier') or synthesis.get('dossier') or '')
    local = await synthesize_campaign_dossier(synthesis, deterministic_dossier=deterministic, enabled=enabled)
    updated = dict(synthesis)
    updated['campaign_synthesis'] = {key: value for key, value in local.items() if key != 'dossier'}
    if local.get('used') and local.get('dossier'):
        updated['dossier'] = local['dossier']
    else:
        updated['dossier'] = deterministic
    manifest = dict(updated.get('manifest') or {})
    manifest['campaign_synthesis'] = updated['campaign_synthesis']
    updated['manifest'] = manifest
    audit = dict(updated.get('audit') or {})
    audit['campaign_synthesis'] = updated['campaign_synthesis']
    updated['audit'] = audit
    return updated


def _campaign_report(
    campaign: dict[str, Any],
    runs: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    *,
    missing_runs: list[dict[str, Any]],
    redact: bool,
) -> str:
    lines = [
        f"# Campaign Dossier: {campaign.get('objective') or campaign.get('campaign_id')}",
        '',
        f"- Campaign ID: {campaign.get('campaign_id')}",
        f"- Status: {campaign.get('status')}",
        f"- Depth: {campaign.get('depth')}",
        f"- Completed runs included: {len(runs)}",
        f"- Deduped sources: {len(sources)}",
        f"- Claims indexed: {len(claims)}",
        f"- Redacted: {'yes' if redact else 'no'}",
        '',
        '## Step Coverage',
        '',
    ]
    for step in campaign.get('steps') or []:
        lines.append(f"- {step.get('step_id')}: {step.get('status')} - {step.get('question')}")
    if missing_runs:
        lines.extend(['', '## Missing Or Incomplete Runs', ''])
        for item in missing_runs:
            lines.append(f"- {item.get('run_id')}: {item.get('message') or item.get('status') or 'unavailable'}")
    lines.extend(['', '## Run Briefs', ''])
    for run in runs:
        metadata = run['metadata']
        payload = run['payload']
        report = _redact_text(payload.get('final_report')) if redact else str(payload.get('final_report') or '')
        excerpt = _report_excerpt(report)
        lines.extend(
            [
                f"### {run['run_id']}",
                '',
                f"- Query: {metadata.get('query')}",
                f"- Research quality: {_quality_label(payload) or 'n/a'} ({_quality_score(payload) or 'n/a'})",
                f"- Sources: {len(payload.get('sources', []) or [])}",
                f"- Claims: {len(payload.get('claims', []) or [])}",
                '',
                excerpt or 'No final report text was saved for this run.',
                '',
            ]
        )
    lines.extend(['## Top Claims', ''])
    if claims:
        for row in claims[:20]:
            support = row.get('supporting_campaign_sources') or 'none'
            conflict = row.get('conflicting_campaign_sources') or 'none'
            lines.append(f"- [{row.get('run_id')}] {row.get('claim')} (support: {support}; conflicts: {conflict})")
    else:
        lines.append('- No extracted claims were available.')
    lines.extend(['', '## Source Index Preview', ''])
    if sources:
        for row in sources[:20]:
            lines.append(f"- source:{row.get('campaign_source_id')} {row.get('title') or 'Untitled'} - {row.get('url') or 'no url'}")
    else:
        lines.append('- No sources were available.')
    lines.append('')
    return '\n'.join(lines)


def _report_excerpt(report: str, *, limit: int = 1800) -> str:
    text = str(report or '').strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + '\n\n[Excerpt truncated in campaign dossier.]'


def _index_markdown(manifest: dict[str, Any]) -> str:
    counts = manifest.get('counts') if isinstance(manifest.get('counts'), dict) else {}
    files = manifest.get('files') if isinstance(manifest.get('files'), dict) else {}
    lines = [
        f"# Campaign Synthesis: {manifest.get('campaign_id')}",
        '',
        f"- Objective: {manifest.get('objective')}",
        f"- Campaign status: {manifest.get('campaign_status')}",
        f"- Redacted: {'yes' if manifest.get('redacted') else 'no'}",
        f"- Completed runs: {counts.get('completed_runs')}",
        f"- Missing runs: {counts.get('missing_runs')}",
        f"- Deduped sources: {counts.get('deduped_sources')}",
        f"- Claims: {counts.get('claims')}",
        '',
        '## Files',
        '',
    ]
    for label, filename in sorted(files.items()):
        lines.append(f"- [{filename}]({filename}) - {label.replace('_', ' ')}")
    lines.append('')
    return '\n'.join(lines)


def write_campaign_synthesis_bundle(
    campaign_id: str,
    *,
    campaign_root: Path,
    jobs_root: Path,
    runs_root: Path,
    output_dir: Path,
    redact: bool = False,
    synthesis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    synthesis = synthesis or build_campaign_synthesis(
        campaign_id,
        campaign_root=campaign_root,
        jobs_root=jobs_root,
        runs_root=runs_root,
        redact=redact,
    )
    if not synthesis.get('ok'):
        return synthesis
    bundle_dir = output_dir.expanduser().resolve() / campaign_id
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / 'dossier.md').write_text(str(synthesis['dossier']), encoding='utf-8')
    (bundle_dir / 'index.md').write_text(_index_markdown(synthesis['manifest']), encoding='utf-8')
    _write_json(bundle_dir / 'manifest.json', synthesis['manifest'])
    _write_json(bundle_dir / 'audit.json', synthesis['audit'])
    _write_csv(
        bundle_dir / 'sources.csv',
        synthesis['sources'],
        ['campaign_source_id', 'first_run_id', 'source_id', 'title', 'url', 'source_type', 'reliability_weight', 'fetched_at', 'rendered'],
    )
    _write_csv(
        bundle_dir / 'claims.csv',
        synthesis['claims'],
        [
            'campaign_claim_id',
            'run_id',
            'claim_id',
            'claim',
            'confidence',
            'supporting_campaign_sources',
            'conflicting_campaign_sources',
            'supporting_evidence_count',
        ],
    )
    result = dict(synthesis)
    result['bundle_dir'] = str(bundle_dir)
    result['files'] = sorted(path.name for path in bundle_dir.iterdir() if path.is_file())
    return result
