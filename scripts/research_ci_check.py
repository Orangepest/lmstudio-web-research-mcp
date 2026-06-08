#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.research_stack_status import DEFAULT_CONFIG_PATH, build_status, format_status
from scripts.run_research_eval import DEFAULT_FIXTURE_ROOT
from scripts.quality_timeline import (
    DEFAULT_OUTPUT as DEFAULT_QUALITY_TIMELINE_OUTPUT,
    collect_quality_timeline,
    timeline_markdown,
)
from web_research.eval import utc_timestamp
from web_research.remediation_benchmarks import run_remediation_learning_benchmark


DEFAULT_OUTPUT_ROOT = ROOT / '.runtime' / 'ci_checks'
DEFAULT_FIXTURE_TASKS = ROOT / 'evals' / 'research_fixture_tasks.json'
DEFAULT_FIXTURE = DEFAULT_FIXTURE_ROOT / 'ci_basic.json'


def _json_default(value: object) -> str:
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


def make_ci_dir(base: Path) -> Path:
    for _attempt in range(10):
        name = f"{utc_timestamp().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"
        path = base / name
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise RuntimeError('Could not create unique CI check output directory.')


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def run_fixture_eval(
    *,
    output_dir: Path,
    tasks: Path,
    fixture: Path,
    min_score: int | None,
    min_average_score: int | None,
    fail_on_labels: list[str],
    limit: int | None = None,
) -> dict[str, Any]:
    eval_output = output_dir / 'fixture_eval'
    command = [
        sys.executable,
        str(ROOT / 'scripts' / 'run_research_eval.py'),
        '--tasks',
        str(tasks),
        '--fixture',
        str(fixture),
        '--output-dir',
        str(eval_output),
    ]
    if min_score is not None:
        command.extend(['--min-score', str(min_score)])
    if min_average_score is not None:
        command.extend(['--min-average-score', str(min_average_score)])
    for label in fail_on_labels:
        command.extend(['--fail-on-label', label])
    if limit is not None:
        command.extend(['--limit', str(limit)])

    completed = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, check=False)
    summary_path = eval_output / 'summary.json'
    summary = _load_json(summary_path)
    thresholds = summary.get('thresholds') if isinstance(summary.get('thresholds'), dict) else {}
    return {
        'ok': completed.returncode == 0 and bool(summary) and thresholds.get('ok', True) is not False,
        'command': command,
        'returncode': completed.returncode,
        'stdout': completed.stdout,
        'stderr': completed.stderr,
        'output_dir': str(eval_output),
        'summary_path': str(summary_path),
        'summary_md_path': str(eval_output / 'summary.md'),
        'summary': summary,
    }


def run_remediation_benchmark(*, output_dir: Path) -> dict[str, Any]:
    result = run_remediation_learning_benchmark()
    path = output_dir / 'remediation_learning_benchmark.json'
    _write_json(path, result)
    return {'ok': bool(result.get('ok')), 'path': str(path), **result}


def refresh_quality_timeline(*, ci_root: Path, output: Path, limit: int = 20) -> dict[str, Any]:
    timeline = collect_quality_timeline(ci_root, limit=max(1, limit))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(timeline_markdown(timeline), encoding='utf-8')
    json_path = output.with_suffix('.json')
    _write_json(json_path, timeline)
    return {
        'ok': bool(timeline.get('ok')),
        'output': str(output),
        'json': str(json_path),
        'event_count': timeline.get('event_count'),
        'failure_count': timeline.get('failure_count'),
        'regression_count': timeline.get('regression_count'),
        'latest_id': timeline.get('latest_id'),
    }


def _stack_summary(status: dict[str, Any] | None) -> dict[str, Any]:
    if not status:
        return {'ok': None, 'tool_count': None, 'prompt_ok': None, 'docs_ok': None, 'config_ok': None}
    tools = status.get('tools') if isinstance(status.get('tools'), dict) else {}
    prompt = status.get('prompt') if isinstance(status.get('prompt'), dict) else {}
    docs = status.get('docs') if isinstance(status.get('docs'), dict) else {}
    config = status.get('config') if isinstance(status.get('config'), dict) else {}
    return {
        'ok': status.get('ok'),
        'tool_count': tools.get('tool_count') or tools.get('expected_tool_count'),
        'missing_tools': tools.get('missing_tools'),
        'unexpected_tools': tools.get('unexpected_tools'),
        'prompt_ok': prompt.get('ok'),
        'docs_ok': docs.get('ok'),
        'config_ok': config.get('ok'),
    }


def _eval_summary(eval_result: dict[str, Any] | None) -> dict[str, Any]:
    if not eval_result:
        return {'ok': None, 'average_score': None, 'task_count': None, 'labels': {}}
    summary = eval_result.get('summary') if isinstance(eval_result.get('summary'), dict) else {}
    thresholds = summary.get('thresholds') if isinstance(summary.get('thresholds'), dict) else {}
    return {
        'ok': eval_result.get('ok'),
        'returncode': eval_result.get('returncode'),
        'average_score': summary.get('average_score'),
        'task_count': summary.get('task_count'),
        'labels': summary.get('labels') if isinstance(summary.get('labels'), dict) else {},
        'threshold_ok': thresholds.get('ok') if thresholds else None,
        'threshold_failures': thresholds.get('failure_count') if thresholds else 0,
    }


def ci_markdown(check: dict[str, Any]) -> str:
    stack = check.get('stack') if isinstance(check.get('stack'), dict) else None
    eval_result = check.get('fixture_eval') if isinstance(check.get('fixture_eval'), dict) else None
    remediation_benchmark = (
        check.get('remediation_learning_benchmark')
        if isinstance(check.get('remediation_learning_benchmark'), dict)
        else None
    )
    quality_timeline = check.get('quality_timeline') if isinstance(check.get('quality_timeline'), dict) else None
    stack_brief = _stack_summary(stack)
    eval_brief = _eval_summary(eval_result)
    lines = [
        '# Research CI Check',
        '',
        f"- Completed at: {check.get('completed_at')}",
        f"- Status: {'pass' if check.get('ok') else 'fail'}",
        f"- Output dir: {check.get('output_dir')}",
        '',
        '## Stack Probe',
        '',
        f"- Enabled: {check.get('probe_tools')}",
        f"- OK: {stack_brief.get('ok')}",
        f"- Tool count: {stack_brief.get('tool_count')}",
        f"- Missing tools: {stack_brief.get('missing_tools')}",
        f"- Unexpected tools: {stack_brief.get('unexpected_tools')}",
        f"- Prompt/docs/config OK: {stack_brief.get('prompt_ok')} / {stack_brief.get('docs_ok')} / {stack_brief.get('config_ok')}",
        '',
        '## Fixture Eval',
        '',
        f"- Enabled: {check.get('run_eval')}",
        f"- OK: {eval_brief.get('ok')}",
        f"- Return code: {eval_brief.get('returncode')}",
        f"- Tasks: {eval_brief.get('task_count')}",
        f"- Average score: {eval_brief.get('average_score')}",
        f"- Labels: {eval_brief.get('labels')}",
        f"- Threshold OK: {eval_brief.get('threshold_ok')}",
        f"- Threshold failures: {eval_brief.get('threshold_failures')}",
    ]
    if eval_result:
        lines.extend(
            [
                f"- Summary JSON: {eval_result.get('summary_path')}",
                f"- Summary Markdown: {eval_result.get('summary_md_path')}",
            ]
        )
    lines.extend(
        [
            '',
            '## Remediation Learning Benchmark',
            '',
            f"- Enabled: {check.get('run_remediation_benchmark')}",
            f"- OK: {remediation_benchmark.get('ok') if remediation_benchmark else None}",
            f"- Scenarios: {remediation_benchmark.get('scenario_count') if remediation_benchmark else None}",
            f"- Passed: {remediation_benchmark.get('passed') if remediation_benchmark else None}",
            f"- Failed: {remediation_benchmark.get('failed') if remediation_benchmark else None}",
        ]
    )
    if remediation_benchmark:
        lines.append(f"- Result JSON: {remediation_benchmark.get('path')}")
    lines.extend(
        [
            '',
            '## Quality Timeline',
            '',
            f"- Enabled: {check.get('refresh_quality_timeline')}",
            f"- OK: {quality_timeline.get('ok') if quality_timeline else None}",
            f"- Events: {quality_timeline.get('event_count') if quality_timeline else None}",
            f"- Failures: {quality_timeline.get('failure_count') if quality_timeline else None}",
            f"- Regressions: {quality_timeline.get('regression_count') if quality_timeline else None}",
        ]
    )
    if quality_timeline:
        lines.extend(
            [
                f"- Timeline Markdown: {quality_timeline.get('output')}",
                f"- Timeline JSON: {quality_timeline.get('json')}",
            ]
        )
    if stack:
        lines.extend(['', '## Stack Status', '', '```text', format_status(stack), '```'])
    return '\n'.join(lines) + '\n'


def build_ci_check(
    *,
    output_dir: Path,
    config_path: Path,
    research_dir: Path,
    runs_root: Path,
    probe_tools: bool,
    run_eval: bool,
    run_remediation_benchmark_check: bool,
    tasks: Path,
    fixture: Path,
    min_score: int | None,
    min_average_score: int | None,
    fail_on_labels: list[str],
    limit: int | None = None,
) -> dict[str, Any]:
    stack = None
    if probe_tools:
        stack = build_status(
            config_path=config_path,
            research_dir=research_dir,
            runs_root=runs_root,
            probe_tools=True,
        )
    eval_result = None
    if run_eval:
        eval_result = run_fixture_eval(
            output_dir=output_dir,
            tasks=tasks,
            fixture=fixture,
            min_score=min_score,
            min_average_score=min_average_score,
            fail_on_labels=fail_on_labels,
            limit=limit,
        )
    remediation_benchmark = None
    if run_remediation_benchmark_check:
        remediation_benchmark = run_remediation_benchmark(output_dir=output_dir)
    ok = (
        (not probe_tools or bool(stack and stack.get('ok')))
        and (not run_eval or bool(eval_result and eval_result.get('ok')))
        and (not run_remediation_benchmark_check or bool(remediation_benchmark and remediation_benchmark.get('ok')))
    )
    return {
        'ok': ok,
        'completed_at': utc_timestamp(),
        'output_dir': str(output_dir),
        'probe_tools': probe_tools,
        'run_eval': run_eval,
        'run_remediation_benchmark': run_remediation_benchmark_check,
        'fixture_tasks': str(tasks),
        'fixture': str(fixture),
        'thresholds': {
            'min_score': min_score,
            'min_average_score': min_average_score,
            'fail_on_labels': fail_on_labels,
        },
        'stack': stack,
        'fixture_eval': eval_result,
        'remediation_learning_benchmark': remediation_benchmark,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Run deterministic CI checks for the local research MCP stack.')
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument('--research-dir', type=Path, default=ROOT)
    parser.add_argument('--runs-root', type=Path, default=ROOT / '.runtime' / 'research_runs')
    parser.add_argument('--skip-probe', action='store_true', help='Skip launching the MCP tool probe.')
    parser.add_argument('--skip-eval', action='store_true', help='Skip deterministic fixture eval.')
    parser.add_argument('--skip-remediation-benchmark', action='store_true', help='Skip remediation strategy-learning benchmark.')
    parser.add_argument('--skip-quality-timeline', action='store_true', help='Skip refreshing the quality timeline artifact.')
    parser.add_argument('--quality-timeline-output', type=Path, default=DEFAULT_QUALITY_TIMELINE_OUTPUT)
    parser.add_argument('--quality-timeline-limit', type=int, default=20)
    parser.add_argument('--tasks', type=Path, default=DEFAULT_FIXTURE_TASKS)
    parser.add_argument('--fixture', type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--min-score', type=int, default=40)
    parser.add_argument('--min-average-score', type=int, default=None)
    parser.add_argument('--fail-on-label', action='append', default=['fail'])
    parser.add_argument('--json', action='store_true', help='Print the full CI check JSON instead of the short result.')
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else make_ci_dir(DEFAULT_OUTPUT_ROOT)
    output_dir.mkdir(parents=True, exist_ok=True)
    check = build_ci_check(
        output_dir=output_dir,
        config_path=args.config.expanduser().resolve(),
        research_dir=args.research_dir.expanduser().resolve(),
        runs_root=args.runs_root.expanduser().resolve(),
        probe_tools=not args.skip_probe,
        run_eval=not args.skip_eval,
        run_remediation_benchmark_check=not args.skip_remediation_benchmark,
        tasks=args.tasks.expanduser().resolve(),
        fixture=args.fixture.expanduser().resolve(),
        min_score=args.min_score,
        min_average_score=args.min_average_score,
        fail_on_labels=list(args.fail_on_label or []),
        limit=args.limit,
    )
    _write_json(output_dir / 'ci_check.json', check)
    quality_timeline = None
    if not args.skip_quality_timeline:
        quality_timeline = refresh_quality_timeline(
            ci_root=output_dir.parent,
            output=args.quality_timeline_output.expanduser().resolve(),
            limit=args.quality_timeline_limit,
        )
    check['refresh_quality_timeline'] = not args.skip_quality_timeline
    check['quality_timeline'] = quality_timeline
    _write_json(output_dir / 'ci_check.json', check)
    (output_dir / 'ci_check.md').write_text(ci_markdown(check), encoding='utf-8')
    if args.json:
        print(json.dumps(check, indent=2, default=_json_default))
    else:
        print(
            json.dumps(
                {
                    'ok': check['ok'],
                    'output_dir': str(output_dir),
                    'report': str(output_dir / 'ci_check.md'),
                    'fixture_eval': (check.get('fixture_eval') or {}).get('summary_md_path'),
                    'remediation_learning_benchmark': (check.get('remediation_learning_benchmark') or {}).get('path'),
                    'quality_timeline': (check.get('quality_timeline') or {}).get('output'),
                },
                indent=2,
            )
        )
    return 0 if check.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
