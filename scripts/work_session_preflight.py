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
from web_research.eval import utc_timestamp
from web_research.profiles import get_work_profile, list_work_profiles


DEFAULT_OUTPUT_ROOT = ROOT / '.runtime' / 'work_preflights'
DEFAULT_REGRESSION_TASKS = ROOT / 'evals' / 'research_regression_tasks.json'
DEFAULT_FIXTURE_TASKS = ROOT / 'evals' / 'research_fixture_tasks.json'
DEFAULT_FIXTURE = ROOT / 'evals' / 'fixtures' / 'ci_basic.json'


def _json_default(value: object) -> str:
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def make_preflight_dir(base: Path) -> Path:
    for _attempt in range(10):
        name = f"{utc_timestamp().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"
        path = base / name
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise RuntimeError('Could not create unique preflight output directory.')


def _count_items(values: list[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return [{'name': name, 'count': count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def summarize_eval_smoke(eval_output: Path) -> dict[str, Any]:
    summary_path = eval_output / 'summary.json'
    summary_md_path = eval_output / 'summary.md'
    summary = _load_json(summary_path)
    if not summary:
        return {
            'ok': False,
            'summary_json_path': str(summary_path),
            'summary_md_path': str(summary_md_path),
            'message': 'Eval summary JSON was not found or could not be parsed.',
        }

    cap_reasons: list[str] = []
    required_check_failures: list[str] = []
    buried_strong_selected_count = 0
    selected_low_value_source_count = 0
    planned_low_value_source_count = 0
    contradiction_resolution_search_count = 0
    for record in summary.get('records', []) or []:
        if not isinstance(record, dict):
            continue
        score = record.get('score') if isinstance(record.get('score'), dict) else {}
        metrics = score.get('metrics') if isinstance(score.get('metrics'), dict) else {}
        for cap in score.get('score_caps', []) or []:
            if isinstance(cap, dict):
                cap_reasons.append(str(cap.get('reason') or 'unknown'))
        for check in score.get('required_check_failures', []) or []:
            required_check_failures.append(str(check))
        buried_strong_selected_count += int(metrics.get('buried_strong_selected_count') or 0)
        selected_low_value_source_count += int(metrics.get('selected_low_value_source_count') or 0)
        planned_low_value_source_count += int(metrics.get('planned_low_value_source_count') or 0)
        contradiction_resolution_search_count += int(metrics.get('contradiction_resolution_search_count') or 0)

    labels = summary.get('labels') if isinstance(summary.get('labels'), dict) else {}
    return {
        'ok': True,
        'summary_json_path': str(summary_path),
        'summary_md_path': str(summary_md_path),
        'task_count': int(summary.get('task_count') or 0),
        'average_score': summary.get('average_score'),
        'labels': labels,
        'score_cap_count': len(cap_reasons),
        'score_caps': _count_items(cap_reasons),
        'required_check_failure_count': len(required_check_failures),
        'failed_required_checks': _count_items(required_check_failures),
        'buried_strong_selected_count': buried_strong_selected_count,
        'selected_low_value_source_count': selected_low_value_source_count,
        'planned_low_value_source_count': planned_low_value_source_count,
        'contradiction_resolution_search_count': contradiction_resolution_search_count,
    }


def risk_assessment(status: dict[str, Any], *, eval_result: dict[str, Any] | None = None) -> dict[str, Any]:
    risks: list[dict[str, str]] = []
    if not status.get('ok'):
        risks.append({'severity': 'high', 'code': 'stack_status_failed', 'message': 'Stack status did not pass.'})
    config = status.get('config') if isinstance(status.get('config'), dict) else {}
    if config.get('compact_results') != 'true':
        risks.append(
            {
                'severity': 'medium',
                'code': 'compact_results_disabled',
                'message': 'MCP compact results are not enabled; local models may receive oversized tool payloads.',
            }
        )
    if config.get('browser_interaction') != 'true':
        risks.append(
            {
                'severity': 'medium',
                'code': 'browser_interaction_disabled',
                'message': 'Browser interaction is not enabled for JS-heavy pages.',
            }
        )
    runs = status.get('runs') if isinstance(status.get('runs'), dict) else {}
    resumable = runs.get('resumable') if isinstance(runs.get('resumable'), list) else []
    if resumable:
        risks.append(
            {
                'severity': 'low',
                'code': 'resumable_runs_pending',
                'message': f'{len(resumable)} interrupted deep-research run(s) are resumable.',
            }
        )
    budget = runs.get('latest_budget_totals') if isinstance(runs.get('latest_budget_totals'), dict) else {}
    if int(budget.get('blocked_source_count') or 0) >= 3:
        risks.append(
            {
                'severity': 'medium',
                'code': 'recent_blocked_sources',
                'message': f"Latest runs include {budget.get('blocked_source_count')} blocked source(s).",
            }
        )
    tools = status.get('tools') if isinstance(status.get('tools'), dict) else {}
    if tools.get('ok') is False:
        risks.append({'severity': 'high', 'code': 'tool_probe_failed', 'message': 'MCP tool probe failed.'})
    if eval_result and eval_result.get('returncode') not in {None, 0}:
        risks.append({'severity': 'high', 'code': 'eval_smoke_failed', 'message': 'Evaluation smoke run failed.'})
    eval_summary = eval_result.get('summary') if isinstance(eval_result, dict) and isinstance(eval_result.get('summary'), dict) else {}
    if int(eval_summary.get('score_cap_count') or 0) > 0:
        risks.append(
            {
                'severity': 'medium',
                'code': 'eval_score_caps_present',
                'message': f"Eval smoke includes {eval_summary.get('score_cap_count')} score cap(s); inspect capped checks before treating scores as strong.",
            }
        )
    if int(eval_summary.get('selected_low_value_source_count') or 0) > 0:
        risks.append(
            {
                'severity': 'medium',
                'code': 'eval_selected_low_value_sources',
                'message': f"Eval smoke selected {eval_summary.get('selected_low_value_source_count')} low-value source(s).",
            }
        )
    severity_rank = {'high': 0, 'medium': 1, 'low': 2}
    risks.sort(key=lambda item: (severity_rank.get(item['severity'], 99), item['code']))
    return {
        'ok': not any(risk['severity'] == 'high' for risk in risks),
        'risk_count': len(risks),
        'high_count': sum(1 for risk in risks if risk['severity'] == 'high'),
        'medium_count': sum(1 for risk in risks if risk['severity'] == 'medium'),
        'low_count': sum(1 for risk in risks if risk['severity'] == 'low'),
        'risks': risks,
    }


def run_eval_smoke(
    *,
    output_dir: Path,
    limit: int,
    min_score: int | None,
    min_average_score: int | None,
    eval_mode: str = 'fixture',
    tasks: Path | None = None,
    fixture: Path | None = None,
) -> dict[str, Any]:
    eval_output = output_dir / 'eval_smoke'
    normalized_mode = eval_mode.strip().lower()
    if normalized_mode not in {'live', 'fixture'}:
        raise ValueError(f'Unsupported eval smoke mode {eval_mode!r}. Expected live or fixture.')
    task_path = tasks or (DEFAULT_FIXTURE_TASKS if normalized_mode == 'fixture' else DEFAULT_REGRESSION_TASKS)
    command = [
        sys.executable,
        str(ROOT / 'scripts' / 'run_research_eval.py'),
        '--tasks',
        str(task_path),
        '--output-dir',
        str(eval_output),
        '--limit',
        str(max(1, int(limit or 1))),
        '--fail-on-label',
        'fail',
    ]
    fixture_path = (fixture or DEFAULT_FIXTURE) if normalized_mode == 'fixture' else None
    if normalized_mode == 'fixture':
        command.extend(['--fixture', str(fixture_path)])
    if min_score is not None:
        command.extend(['--min-score', str(min_score)])
    if min_average_score is not None:
        command.extend(['--min-average-score', str(min_average_score)])
    completed = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, check=False)
    summary = summarize_eval_smoke(eval_output)
    return {
        'command': command,
        'returncode': completed.returncode,
        'stdout': completed.stdout,
        'stderr': completed.stderr,
        'mode': normalized_mode,
        'tasks_path': str(task_path),
        'fixture_path': str(fixture_path) if fixture_path else None,
        'output_dir': str(eval_output),
        'summary_path': str(eval_output / 'summary.md'),
        'summary_json_path': str(eval_output / 'summary.json'),
        'summary': summary,
    }


def preflight_markdown(preflight: dict[str, Any]) -> str:
    risk = preflight.get('risk') if isinstance(preflight.get('risk'), dict) else {}
    status = preflight.get('status') if isinstance(preflight.get('status'), dict) else {}
    eval_smoke = preflight.get('eval_smoke') if isinstance(preflight.get('eval_smoke'), dict) else None
    lines = [
        '# Work Session Preflight',
        '',
        f"- Completed at: {preflight.get('completed_at')}",
        f"- Status: {'pass' if preflight.get('ok') else 'check'}",
        f"- Output dir: {preflight.get('output_dir')}",
        f"- Stack OK: {status.get('ok')}",
        f"- Risk count: {risk.get('risk_count', 0)}",
        '',
        '## Risks',
        '',
    ]
    risks = risk.get('risks') if isinstance(risk.get('risks'), list) else []
    if not risks:
        lines.append('- No preflight risks flagged.')
    for item in risks:
        if isinstance(item, dict):
            lines.append(f"- {item.get('severity')}: {item.get('code')} - {item.get('message')}")
    lines.extend(['', '## Stack Status', '', '```text', format_status(status), '```'])
    if eval_smoke:
        eval_summary = eval_smoke.get('summary') if isinstance(eval_smoke.get('summary'), dict) else {}
        labels = eval_summary.get('labels') if isinstance(eval_summary.get('labels'), dict) else {}
        label_text = ', '.join(f'{key}:{value}' for key, value in sorted(labels.items())) or 'n/a'
        lines.extend(
            [
                '',
                '## Eval Smoke',
                '',
                f"- Return code: {eval_smoke.get('returncode')}",
                f"- Mode: {eval_smoke.get('mode', 'live')}",
                f"- Tasks file: {eval_smoke.get('tasks_path', 'n/a')}",
                f"- Fixture: {eval_smoke.get('fixture_path') or 'n/a'}",
                f"- Output dir: {eval_smoke.get('output_dir')}",
                f"- Summary: {eval_smoke.get('summary_path')}",
                f"- Tasks: {eval_summary.get('task_count', 'n/a')}",
                f"- Average score: {eval_summary.get('average_score', 'n/a')}/100",
                f"- Labels: {label_text}",
                f"- Score caps: {eval_summary.get('score_cap_count', 'n/a')}",
                f"- Failed required checks: {eval_summary.get('required_check_failure_count', 'n/a')}",
                (
                    '- Source selection: '
                    f"{eval_summary.get('buried_strong_selected_count', 'n/a')} buried strong selected / "
                    f"{eval_summary.get('selected_low_value_source_count', 'n/a')} low-value selected / "
                    f"{eval_summary.get('planned_low_value_source_count', 'n/a')} low-value planned"
                ),
                f"- Contradiction-resolution searches: {eval_summary.get('contradiction_resolution_search_count', 'n/a')}",
            ]
        )
        score_caps = eval_summary.get('score_caps') if isinstance(eval_summary.get('score_caps'), list) else []
        if score_caps:
            lines.extend(['', '### Score Caps', ''])
            for item in score_caps:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('name')}: {item.get('count')}")
        failed_checks = (
            eval_summary.get('failed_required_checks')
            if isinstance(eval_summary.get('failed_required_checks'), list)
            else []
        )
        if failed_checks:
            lines.extend(['', '### Failed Required Checks', ''])
            for item in failed_checks:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('name')}: {item.get('count')}")
    lines.append('')
    return '\n'.join(lines)


def build_preflight(
    *,
    output_dir: Path,
    config_path: Path,
    research_dir: Path,
    runs_root: Path,
    probe_tools: bool,
    dry_run: bool,
    eval_smoke: bool,
    eval_limit: int,
    min_score: int | None,
    min_average_score: int | None,
    eval_mode: str = 'fixture',
    eval_tasks: Path | None = None,
    eval_fixture: Path | None = None,
    profile: str = 'careful',
) -> dict[str, Any]:
    work_profile = get_work_profile(profile)
    status = build_status(
        config_path=config_path,
        research_dir=research_dir,
        runs_root=runs_root,
        probe_tools=False if dry_run else probe_tools,
    )
    if dry_run:
        status['dry_run'] = {'enabled': True, 'message': 'No MCP server process was launched.'}
    eval_result = None
    if eval_smoke:
        eval_result = run_eval_smoke(
            output_dir=output_dir,
            limit=eval_limit,
            min_score=min_score,
            min_average_score=min_average_score,
            eval_mode=eval_mode,
            tasks=eval_tasks,
            fixture=eval_fixture,
        )
    risk = risk_assessment(status, eval_result=eval_result)
    return {
        'ok': bool(status.get('ok')) and bool(risk.get('ok')) and (not eval_result or eval_result.get('returncode') == 0),
        'completed_at': utc_timestamp(),
        'output_dir': str(output_dir),
        'dry_run': dry_run,
        'probe_tools': probe_tools and not dry_run,
        'profile': work_profile.to_dict(),
        'status': status,
        'risk': risk,
        'eval_smoke': eval_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Create a work-session preflight report for the local research MCP stack.')
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument('--research-dir', type=Path, default=ROOT)
    parser.add_argument('--runs-root', type=Path, default=ROOT / '.runtime' / 'research_runs')
    parser.add_argument('--probe-tools', action='store_true', help='Launch the MCP server and verify tool listing.')
    parser.add_argument('--dry-run', action='store_true', help='Skip the MCP server probe even if --probe-tools is present.')
    parser.add_argument('--eval-smoke', action='store_true', help='Run a small eval smoke check after status checks.')
    parser.add_argument(
        '--eval-mode',
        choices=['fixture', 'live'],
        default='fixture',
        help='Use deterministic fixture evals by default; live keeps the older web-backed regression smoke.',
    )
    parser.add_argument('--eval-tasks', type=Path, default=None, help='Override the eval smoke task file.')
    parser.add_argument('--eval-fixture', type=Path, default=None, help='Override the fixture file used by --eval-mode fixture.')
    parser.add_argument('--eval-limit', type=int, default=None)
    parser.add_argument('--min-score', type=int, default=None)
    parser.add_argument('--min-average-score', type=int, default=None)
    parser.add_argument('--profile', choices=[item['name'] for item in list_work_profiles()], default='careful')
    parser.add_argument('--list-profiles', action='store_true')
    args = parser.parse_args()
    if args.list_profiles:
        print(json.dumps({'profiles': list_work_profiles()}, indent=2))
        return 0

    profile = get_work_profile(args.profile)
    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else make_preflight_dir(DEFAULT_OUTPUT_ROOT)
    output_dir.mkdir(parents=True, exist_ok=True)
    preflight = build_preflight(
        output_dir=output_dir,
        config_path=args.config.expanduser().resolve(),
        research_dir=args.research_dir.expanduser().resolve(),
        runs_root=args.runs_root.expanduser().resolve(),
        probe_tools=args.probe_tools or profile.probe_tools,
        dry_run=args.dry_run,
        eval_smoke=args.eval_smoke or profile.eval_smoke,
        eval_limit=args.eval_limit if args.eval_limit is not None else profile.eval_limit,
        min_score=args.min_score if args.min_score is not None else profile.min_score,
        min_average_score=args.min_average_score if args.min_average_score is not None else profile.min_average_score,
        eval_mode=args.eval_mode,
        eval_tasks=args.eval_tasks.expanduser().resolve() if args.eval_tasks else None,
        eval_fixture=args.eval_fixture.expanduser().resolve() if args.eval_fixture else None,
        profile=profile.name,
    )
    _write_json(output_dir / 'preflight.json', preflight)
    (output_dir / 'preflight.md').write_text(preflight_markdown(preflight), encoding='utf-8')
    print(json.dumps({'ok': preflight['ok'], 'output_dir': str(output_dir), 'report': str(output_dir / 'preflight.md')}, indent=2))
    return 0 if preflight.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
