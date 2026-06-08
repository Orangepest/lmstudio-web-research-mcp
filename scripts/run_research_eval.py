#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mcp_server.server as server_module
import web_research.service as service_module
from web_research.eval import build_eval_record, human_review_template, load_eval_tasks, utc_timestamp
from web_research.profiles import WorkProfile, get_work_profile, list_work_profiles
from web_research.service import research_web


DEFAULT_TASKS = ROOT / 'evals' / 'research_tasks.json'
DEFAULT_OUTPUT_ROOT = ROOT / '.runtime' / 'evals'
DEFAULT_FIXTURE_ROOT = ROOT / 'evals' / 'fixtures'


def _json_default(value: object) -> str:
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


def _slug(value: str) -> str:
    return ''.join(char if char.isalnum() or char in '-_' else '-' for char in value.lower()).strip('-')[:80] or 'task'


def parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on'}:
            return True
        if normalized in {'0', 'false', 'no', 'off', ''}:
            return False
    raise ValueError(f'Expected boolean value, got {value!r}')


def make_output_dir(base: Path) -> Path:
    for _attempt in range(10):
        run_name = f"{utc_timestamp().replace(':', '').replace('-', '').replace('Z', 'Z')}-{uuid.uuid4().hex[:6]}"
        output_dir = base / run_name
        try:
            output_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return output_dir
    raise RuntimeError('Could not create unique eval output directory.')


def _load_fixture(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.loads(path.expanduser().resolve().read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Fixture file must contain an object: {path}')
    return value


def _fixture_search_payload(query: str, fixture: dict[str, Any], *, max_results: int, site: str | None = None) -> dict[str, Any]:
    searches = fixture.get('searches') if isinstance(fixture.get('searches'), dict) else {}
    query_key = ' '.join(str(query or '').split())
    payload = searches.get(query_key)
    if payload is None and site:
        payload = searches.get(f'{query_key} site:{site}')
    if payload is None:
        payload = searches.get('*')
    if payload is None:
        return {'ok': True, 'query': query, 'provider': 'fixture', 'results': [], 'backend_attempts': []}
    if isinstance(payload, list):
        results = payload
        provider = 'fixture'
    elif isinstance(payload, dict):
        results = payload.get('results', []) if isinstance(payload.get('results'), list) else []
        provider = str(payload.get('provider') or 'fixture')
    else:
        raise ValueError(f'Invalid fixture search payload for query: {query}')
    normalized_results = []
    for index, result in enumerate(results[:max_results], start=1):
        if not isinstance(result, dict):
            continue
        item = dict(result)
        item.setdefault('rank', index)
        item.setdefault('source', item.get('url', ''))
        normalized_results.append(item)
    return {
        'ok': True,
        'query': query,
        'provider': provider,
        'results': normalized_results,
        'backend_attempts': [{'provider': provider, 'ok': True, 'result_count': len(normalized_results), 'latency_seconds': 0}],
        'fixture': True,
    }


def _fixture_read_payload(
    url: str,
    fixture: dict[str, Any],
    *,
    query: str | None,
    render: bool,
    source_id: int,
) -> dict[str, Any]:
    reads = fixture.get('reads') if isinstance(fixture.get('reads'), dict) else {}
    payload = reads.get(url)
    if payload is None:
        return {'ok': False, 'url': url, 'message': f'Fixture has no read payload for {url}', 'fixture': True}
    if not isinstance(payload, dict):
        raise ValueError(f'Invalid fixture read payload for URL: {url}')
    item = dict(payload)
    item.setdefault('ok', True)
    item.setdefault('source_id', source_id)
    item['source_id'] = source_id
    item.setdefault('url', url)
    item.setdefault('final_url', item.get('url') or url)
    item.setdefault('title', item.get('final_url') or url)
    item.setdefault('text', item.get('summary') or '')
    item.setdefault('rendered', render)
    item['fixture'] = True
    evidence = []
    for index, evidence_item in enumerate(item.get('evidence', []) or [], start=1):
        if not isinstance(evidence_item, dict):
            continue
        remapped = dict(evidence_item)
        remapped['source_id'] = source_id
        remapped.setdefault('url', item.get('final_url') or url)
        remapped.setdefault('title', item.get('title') or item.get('final_url') or url)
        quote = str(remapped.get('quote') or remapped.get('text') or '')
        if quote and not remapped.get('char_range'):
            remapped['char_range'] = [0, len(quote)]
        if remapped.get('char_range') and not remapped.get('citation'):
            start, end = remapped['char_range']
            remapped['citation'] = f'source:{source_id}[{start}:{end}]'
        remapped.setdefault('rank', index)
        evidence.append(remapped)
    if not evidence and item.get('text'):
        quote = str(item.get('text') or '')[:500]
        evidence.append(
            {
                'source_id': source_id,
                'url': item.get('final_url') or url,
                'title': item.get('title') or item.get('final_url') or url,
                'quote': quote,
                'char_range': [0, len(quote)],
                'citation': f'source:{source_id}[0:{len(quote)}]',
                'rank': 1,
            }
        )
    item['evidence'] = evidence
    return item


@contextmanager
def fixture_runtime(fixture: dict[str, Any]):
    if not fixture:
        yield
        return

    def fake_search(query: str, max_results: int = 10, freshness: str | None = None, site: str | None = None) -> dict:
        return _fixture_search_payload(query, fixture, max_results=max_results, site=site)

    async def fake_read_url(url: str, query: str | None = None, render: bool = False, source_id: int = 1) -> dict:
        return _fixture_read_payload(url, fixture, query=query, render=render, source_id=source_id)

    with patch.object(service_module, 'web_search', side_effect=fake_search), patch.object(
        service_module,
        'read_url',
        side_effect=fake_read_url,
    ):
        yield


async def run_task(task: dict[str, Any], *, fixture: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    params = dict(task.get('params') or {})
    start = time.monotonic()
    tool = task.get('tool') or 'research_web'
    with fixture_runtime(fixture or {}):
        if tool == 'deep_research':
            payload = await server_module._run_deep_research(
                task['question'],
                breadth=int(params.get('breadth', 3)),
                read_top_per_query=int(params.get('read_top_per_query', 1)),
                freshness=params.get('freshness'),
                render=parse_bool(params.get('render'), default=False),
                report_format=str(params.get('report_format', 'executive_brief')),
                follow_up_rounds=int(params.get('follow_up_rounds', 1)),
            )
        elif tool == 'research_web':
            payload = await research_web(
                query=task['question'],
                max_results=int(params.get('max_results', 8)),
                read_top=int(params.get('read_top', 3)),
                freshness=params.get('freshness'),
                site=params.get('site'),
                render=parse_bool(params.get('render'), default=False),
                report_format=str(params.get('report_format', 'executive_brief')),
            )
        else:
            raise ValueError(f'Unsupported eval tool for {task["id"]}: {tool}')
    if fixture:
        payload['eval_fixture'] = {'enabled': True, 'name': fixture.get('name') or fixture.get('id') or 'fixture'}
    elapsed = time.monotonic() - start
    return payload, build_eval_record(task, payload, elapsed_seconds=elapsed)


def task_with_profile_defaults(task: dict[str, Any], profile: WorkProfile | None) -> dict[str, Any]:
    if profile is None:
        return task
    merged = dict(task)
    params = dict(task.get('params') or {})
    tool = str(task.get('tool') or 'research_web')
    if tool == 'deep_research':
        params.setdefault('breadth', profile.research_breadth)
        params.setdefault('read_top_per_query', profile.read_top_per_query)
        params.setdefault('follow_up_rounds', profile.follow_up_rounds)
        params.setdefault('report_format', profile.report_format)
        params.setdefault('render', profile.render)
    elif tool == 'research_web':
        params.setdefault('max_results', max(profile.research_breadth * 2, 4))
        params.setdefault('read_top', profile.read_top_per_query)
        params.setdefault('report_format', profile.report_format)
        params.setdefault('render', profile.render)
    merged['params'] = params
    merged['profile'] = profile.name
    return merged


def build_threshold_report(
    records: list[dict[str, Any]],
    *,
    average_score: float,
    min_score: int | None = None,
    min_average_score: int | None = None,
    fail_on_labels: list[str] | None = None,
) -> dict[str, Any]:
    normalized_fail_labels = sorted({label.strip().lower() for label in fail_on_labels or [] if label.strip()})
    failures = []
    if min_average_score is not None and average_score < min_average_score:
        failures.append(
            {
                'type': 'average_score',
                'message': f'Average score {average_score}/100 is below threshold {min_average_score}/100.',
            }
        )
    for record in records:
        task = record.get('task') if isinstance(record.get('task'), dict) else {}
        score = record.get('score') if isinstance(record.get('score'), dict) else {}
        task_id = str(task.get('id') or 'unknown')
        score_value = int(score.get('score') or 0)
        label = str(score.get('label') or 'unknown').lower()
        if min_score is not None and score_value < min_score:
            failures.append(
                {
                    'type': 'task_score',
                    'task_id': task_id,
                    'score': score_value,
                    'threshold': min_score,
                    'message': f'{task_id} scored {score_value}/100 below threshold {min_score}/100.',
                }
            )
        if label in normalized_fail_labels:
            failures.append(
                {
                    'type': 'task_label',
                    'task_id': task_id,
                    'label': label,
                    'message': f'{task_id} has failing label {label}.',
                }
            )
    return {
        'ok': not failures,
        'min_score': min_score,
        'min_average_score': min_average_score,
        'fail_on_labels': normalized_fail_labels,
        'failure_count': len(failures),
        'failures': failures,
    }


def build_summary(records: list[dict[str, Any]], *, output_dir: Path) -> dict[str, Any]:
    scores = [int(record.get('score', {}).get('score') or 0) for record in records]
    labels: dict[str, int] = {}
    for record in records:
        label = str(record.get('score', {}).get('label') or 'unknown')
        labels[label] = labels.get(label, 0) + 1
    weakest_checks: dict[str, int] = {}
    for record in records:
        for check in record.get('score', {}).get('weakest_checks', []) or []:
            weakest_checks[str(check)] = weakest_checks.get(str(check), 0) + 1
    return {
        'ok': True,
        'completed_at': utc_timestamp(),
        'output_dir': str(output_dir),
        'task_count': len(records),
        'average_score': round(sum(scores) / len(scores), 1) if scores else 0,
        'labels': labels,
        'weakest_checks': [
            {'check': check, 'count': count}
            for check, count in sorted(weakest_checks.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        'records': records,
    }


def summary_markdown(summary: dict[str, Any]) -> str:
    thresholds = summary.get('thresholds') if isinstance(summary.get('thresholds'), dict) else {}
    lines = [
        '# Research Eval Summary',
        '',
        f"- Completed at: {summary.get('completed_at')}",
        f"- Tasks: {summary.get('task_count')}",
        f"- Average score: {summary.get('average_score')}/100",
        f"- Output dir: {summary.get('output_dir')}",
        '',
        '| Task | Category | Score | Sources | Primary | Claim Support | Intent Quality | Source Selection | Contradiction Table | Weakest Checks |',
        '| --- | --- | ---: | ---: | ---: | --- | ---: | --- | --- | --- |',
    ]
    for record in summary.get('records', []) or []:
        task = record.get('task') or {}
        score = record.get('score') or {}
        metrics = score.get('metrics') or {}
        weakest = ', '.join(str(item) for item in (score.get('weakest_checks') or [])[:3])
        claim_support = (
            f"{metrics.get('indexed_supported_claim_count', 0)} supported / "
            f"{metrics.get('indexed_unsupported_claim_count', 0)} unsupported"
        )
        contradiction_table = (
            f"{metrics.get('conflicted_claim_count', 0)} conflicted / "
            f"{metrics.get('contradiction_table_row_count', 0)} rows / "
            f"{metrics.get('contradiction_table_resolution_query_count', 0)} queries"
        )
        source_selection = (
            f"{metrics.get('buried_strong_selected_count', 0)} buried strong / "
            f"{metrics.get('selected_low_value_source_count', 0)} low-value"
        )
        lines.append(
            '| {task} | {category} | {label} {score}/100 | {sources} | {primary} | {claim_support} | {intent_quality}/100 | {source_selection} | {contradiction_table} | {weakest} |'.format(
                task=task.get('id', ''),
                category=task.get('category', ''),
                label=score.get('label', ''),
                score=score.get('score', 0),
                sources=metrics.get('source_count', 0),
                primary=metrics.get('primary_source_count', 0),
                claim_support=claim_support,
                intent_quality=metrics.get('average_intent_quality_score', 0),
                source_selection=source_selection,
                contradiction_table=contradiction_table,
                weakest=weakest,
            )
        )
    weakest_summary = summary.get('weakest_checks') if isinstance(summary.get('weakest_checks'), list) else []
    if weakest_summary:
        lines.extend(['', '## Common Weaknesses', ''])
        for item in weakest_summary[:10]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('check')}: {item.get('count')}")
    if thresholds:
        lines.extend(
            [
                '',
                '## Thresholds',
                '',
                f"- Status: {'pass' if thresholds.get('ok') else 'fail'}",
                f"- Minimum task score: {thresholds.get('min_score') if thresholds.get('min_score') is not None else 'n/a'}",
                (
                    '- Minimum average score: '
                    f"{thresholds.get('min_average_score') if thresholds.get('min_average_score') is not None else 'n/a'}"
                ),
                f"- Fail on labels: {', '.join(thresholds.get('fail_on_labels', []) or []) or 'none'}",
                f"- Failures: {thresholds.get('failure_count', 0)}",
            ]
        )
        for failure in thresholds.get('failures', []) or []:
            if isinstance(failure, dict):
                lines.append(f"- {failure.get('message', '')}")
    return '\n'.join(lines) + '\n'


async def main_async() -> int:
    parser = argparse.ArgumentParser(description='Run the local research evaluation harness.')
    parser.add_argument('--tasks', type=Path, default=DEFAULT_TASKS, help='Path to eval task JSON.')
    parser.add_argument('--output-dir', type=Path, default=None, help='Directory for eval artifacts.')
    parser.add_argument('--limit', type=int, default=None, help='Run only the first N tasks.')
    parser.add_argument('--task-id', action='append', default=None, help='Run only matching task id. Can be repeated.')
    parser.add_argument('--min-score', type=int, default=None, help='Fail if any task scores below this value.')
    parser.add_argument('--min-average-score', type=int, default=None, help='Fail if the average score is below this value.')
    parser.add_argument('--fail-on-label', action='append', default=None, help='Fail if any task receives this score label.')
    parser.add_argument('--profile', choices=[item['name'] for item in list_work_profiles()], default=None)
    parser.add_argument('--fixture', type=Path, default=None, help='Use deterministic search/read fixture JSON instead of live web.')
    parser.add_argument('--list-profiles', action='store_true', help='Print available work profiles and exit.')
    args = parser.parse_args()

    if args.list_profiles:
        print(json.dumps({'ok': True, 'profiles': list_work_profiles()}, indent=2))
        return 0

    profile = get_work_profile(args.profile) if args.profile else None
    fixture = _load_fixture(args.fixture) if args.fixture else {}
    eval_limit = args.limit if args.limit is not None else (profile.eval_limit if profile else None)
    min_score = args.min_score if args.min_score is not None else (profile.min_score if profile else None)
    min_average_score = (
        args.min_average_score if args.min_average_score is not None else (profile.min_average_score if profile else None)
    )
    fail_on_labels = list(args.fail_on_label or [])
    if profile and profile.eval_smoke and not fail_on_labels:
        fail_on_labels = ['fail']

    tasks = load_eval_tasks(args.tasks)
    if args.task_id:
        selected = set(args.task_id)
        tasks = [task for task in tasks if task['id'] in selected]
    if eval_limit is not None:
        tasks = tasks[: max(0, eval_limit)]
    tasks = [task_with_profile_defaults(task, profile) for task in tasks]
    if not tasks:
        raise SystemExit('No eval tasks selected.')

    if args.output_dir:
        output_dir = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = make_output_dir(DEFAULT_OUTPUT_ROOT)

    records = []
    for index, task in enumerate(tasks, start=1):
        print(f'[{index}/{len(tasks)}] {task["id"]}: {task["question"]}', flush=True)
        task_dir = output_dir / _slug(task['id'])
        task_dir.mkdir(parents=True, exist_ok=True)
        try:
            payload, record = await run_task(task, fixture=fixture)
        except Exception as exc:
            payload = {'ok': False, 'message': str(exc)}
            record = {
                'task': task,
                'completed_at': utc_timestamp(),
                'elapsed_seconds': 0,
                'ok': False,
                'error': str(exc),
                'score': {'score': 0, 'label': 'fail', 'checks': {}, 'metrics': {}},
            }
        _write_json(task_dir / 'payload.json', payload)
        _write_json(task_dir / 'record.json', record)
        if isinstance(payload.get('final_report'), str):
            (task_dir / 'report.md').write_text(payload['final_report'], encoding='utf-8')
        (task_dir / 'review.md').write_text(human_review_template(record), encoding='utf-8')
        records.append(record)

    summary = build_summary(records, output_dir=output_dir)
    if min_score is not None or min_average_score is not None or fail_on_labels:
        summary['thresholds'] = build_threshold_report(
            records,
            average_score=float(summary.get('average_score') or 0),
            min_score=min_score,
            min_average_score=min_average_score,
            fail_on_labels=fail_on_labels,
        )
    _write_json(output_dir / 'summary.json', summary)
    (output_dir / 'summary.md').write_text(summary_markdown(summary), encoding='utf-8')
    print(f'Wrote eval summary: {output_dir / "summary.md"}', flush=True)
    if isinstance(summary.get('thresholds'), dict) and not summary['thresholds'].get('ok'):
        print(f'Eval thresholds failed: {summary["thresholds"].get("failure_count", 0)} issue(s)', file=sys.stderr)
        return 1
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == '__main__':
    raise SystemExit(main())
