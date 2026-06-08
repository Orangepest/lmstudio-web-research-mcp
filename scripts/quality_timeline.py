#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


DEFAULT_CI_CHECK_ROOT = ROOT / '.runtime' / 'ci_checks'
DEFAULT_OUTPUT = ROOT / '.runtime' / 'quality_timeline.md'


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _labels_text(labels: object) -> str:
    if not isinstance(labels, dict) or not labels:
        return 'none'
    return ', '.join(f'{key}:{value}' for key, value in sorted(labels.items()))


def _stack_summary(stack: dict[str, Any]) -> dict[str, Any]:
    prompt = stack.get('prompt') if isinstance(stack.get('prompt'), dict) else {}
    docs = stack.get('docs') if isinstance(stack.get('docs'), dict) else {}
    config = stack.get('config') if isinstance(stack.get('config'), dict) else {}
    tools = stack.get('tools') if isinstance(stack.get('tools'), dict) else {}
    search = stack.get('search') if isinstance(stack.get('search'), dict) else {}
    recent_failures = search.get('recent_failures') if isinstance(search.get('recent_failures'), dict) else {}
    return {
        'ok': stack.get('ok'),
        'prompt_ok': prompt.get('ok'),
        'docs_ok': docs.get('ok'),
        'config_ok': config.get('ok'),
        'tool_count': tools.get('tool_count') or tools.get('expected_tool_count'),
        'missing_tool_count': len(tools.get('missing_tools') or []),
        'unexpected_tool_count': len(tools.get('unexpected_tools') or []),
        'search_ok': search.get('ok'),
        'search_failure_count': _as_int(recent_failures.get('count')),
    }


def _fixture_eval_summary(fixture_eval: dict[str, Any]) -> dict[str, Any]:
    summary = fixture_eval.get('summary') if isinstance(fixture_eval.get('summary'), dict) else {}
    thresholds = summary.get('thresholds') if isinstance(summary.get('thresholds'), dict) else {}
    labels = summary.get('labels') if isinstance(summary.get('labels'), dict) else {}
    return {
        'ok': fixture_eval.get('ok'),
        'returncode': fixture_eval.get('returncode'),
        'average_score': _as_float(summary.get('average_score')),
        'task_count': _as_int(summary.get('task_count')),
        'labels': labels,
        'fail_label_count': _as_int(labels.get('fail')) if isinstance(labels, dict) else 0,
        'threshold_ok': thresholds.get('ok') if thresholds else None,
        'threshold_failure_count': _as_int(thresholds.get('failure_count')) if thresholds else 0,
        'summary_path': fixture_eval.get('summary_path'),
        'summary_md_path': fixture_eval.get('summary_md_path'),
    }


def _remediation_summary(remediation: dict[str, Any]) -> dict[str, Any]:
    records = remediation.get('records') if isinstance(remediation.get('records'), list) else []
    failed_records = [item for item in records if isinstance(item, dict) and not item.get('ok')]
    return {
        'ok': remediation.get('ok'),
        'scenario_count': _as_int(remediation.get('scenario_count'), len(records)),
        'passed': _as_int(remediation.get('passed')),
        'failed': _as_int(remediation.get('failed'), len(failed_records)),
        'failed_scenarios': [str(item.get('id') or 'unknown') for item in failed_records],
        'path': remediation.get('path'),
    }


def _risk_flags(event: dict[str, Any]) -> list[str]:
    flags = []
    if event.get('ok') is False:
        flags.append('ci_failed')
    if event.get('stack_ok') is False:
        flags.append('stack_failed')
    if event.get('fixture_eval_ok') is False:
        flags.append('fixture_eval_failed')
    if event.get('remediation_ok') is False:
        flags.append('remediation_failed')
    if _as_int(event.get('fixture_eval_fail_label_count')) > 0:
        flags.append('eval_fail_labels')
    if _as_int(event.get('fixture_eval_threshold_failure_count')) > 0:
        flags.append('eval_threshold_failures')
    if _as_int(event.get('remediation_failed')) > 0:
        flags.append('remediation_scenarios_failed')
    if event.get('fixture_eval_average_delta') is not None and float(event.get('fixture_eval_average_delta') or 0) < 0:
        flags.append('eval_score_drop')
    if event.get('remediation_failed_delta') is not None and _as_int(event.get('remediation_failed_delta')) > 0:
        flags.append('remediation_regression')
    if _as_int(event.get('stack_search_failure_count')) > 0:
        flags.append('search_provider_failures')
    return sorted(set(flags))


def _ci_event(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    stack = payload.get('stack') if isinstance(payload.get('stack'), dict) else {}
    fixture_eval = payload.get('fixture_eval') if isinstance(payload.get('fixture_eval'), dict) else {}
    remediation = (
        payload.get('remediation_learning_benchmark')
        if isinstance(payload.get('remediation_learning_benchmark'), dict)
        else {}
    )
    stack_brief = _stack_summary(stack)
    eval_brief = _fixture_eval_summary(fixture_eval)
    remediation_brief = _remediation_summary(remediation)
    report_path = path.with_name('ci_check.md')
    return {
        'id': path.parent.name,
        'completed_at': payload.get('completed_at'),
        'path': str(path),
        'report_path': str(report_path if report_path.exists() else path),
        'ok': bool(payload.get('ok')),
        'stack_ok': stack_brief.get('ok'),
        'stack_prompt_ok': stack_brief.get('prompt_ok'),
        'stack_docs_ok': stack_brief.get('docs_ok'),
        'stack_config_ok': stack_brief.get('config_ok'),
        'stack_tool_count': stack_brief.get('tool_count'),
        'stack_missing_tool_count': stack_brief.get('missing_tool_count'),
        'stack_unexpected_tool_count': stack_brief.get('unexpected_tool_count'),
        'stack_search_ok': stack_brief.get('search_ok'),
        'stack_search_failure_count': stack_brief.get('search_failure_count'),
        'fixture_eval_ok': eval_brief.get('ok'),
        'fixture_eval_returncode': eval_brief.get('returncode'),
        'fixture_eval_average_score': eval_brief.get('average_score'),
        'fixture_eval_task_count': eval_brief.get('task_count'),
        'fixture_eval_labels': eval_brief.get('labels'),
        'fixture_eval_fail_label_count': eval_brief.get('fail_label_count'),
        'fixture_eval_threshold_ok': eval_brief.get('threshold_ok'),
        'fixture_eval_threshold_failure_count': eval_brief.get('threshold_failure_count'),
        'fixture_eval_summary_path': eval_brief.get('summary_path'),
        'fixture_eval_summary_md_path': eval_brief.get('summary_md_path'),
        'remediation_ok': remediation_brief.get('ok'),
        'remediation_scenario_count': remediation_brief.get('scenario_count'),
        'remediation_passed': remediation_brief.get('passed'),
        'remediation_failed': remediation_brief.get('failed'),
        'remediation_failed_scenarios': remediation_brief.get('failed_scenarios'),
        'remediation_path': remediation_brief.get('path'),
    }


def collect_quality_timeline(ci_root: Path, *, limit: int = 20) -> dict[str, Any]:
    events = []
    for path in sorted(ci_root.glob('*/ci_check.json')):
        payload = _load_json(path)
        if not payload:
            continue
        events.append(_ci_event(path, payload))

    events.sort(key=lambda item: (str(item.get('completed_at') or ''), str(item.get('id') or '')))
    previous: dict[str, Any] | None = None
    for event in events:
        if previous:
            current_average = event.get('fixture_eval_average_score')
            previous_average = previous.get('fixture_eval_average_score')
            if current_average is not None and previous_average is not None:
                event['fixture_eval_average_delta'] = round(float(current_average) - float(previous_average), 3)
            else:
                event['fixture_eval_average_delta'] = None
            if event.get('remediation_ok') is not None and previous.get('remediation_ok') is not None:
                event['remediation_failed_delta'] = _as_int(event.get('remediation_failed')) - _as_int(
                    previous.get('remediation_failed')
                )
            else:
                event['remediation_failed_delta'] = None
            event['stack_ok_changed'] = (
                event.get('stack_ok') is not None
                and previous.get('stack_ok') is not None
                and event.get('stack_ok') != previous.get('stack_ok')
            )
        else:
            event['fixture_eval_average_delta'] = None
            event['remediation_failed_delta'] = None
            event['stack_ok_changed'] = False
        event['risk_flags'] = _risk_flags(event)
        previous = event

    recent_events = list(reversed(events))[:limit]
    latest = recent_events[0] if recent_events else {}
    regression_count = sum(
        1
        for event in recent_events
        if 'eval_score_drop' in (event.get('risk_flags') or [])
        or 'remediation_regression' in (event.get('risk_flags') or [])
        or event.get('stack_ok_changed')
    )
    failure_count = sum(1 for event in recent_events if not event.get('ok'))
    return {
        'ok': bool(latest.get('ok')) if latest else True,
        'ci_root': str(ci_root),
        'event_count': len(recent_events),
        'total_event_count': len(events),
        'failure_count': failure_count,
        'regression_count': regression_count,
        'latest_id': latest.get('id'),
        'latest_completed_at': latest.get('completed_at'),
        'latest_stack_ok': latest.get('stack_ok'),
        'latest_fixture_eval_ok': latest.get('fixture_eval_ok'),
        'latest_remediation_ok': latest.get('remediation_ok'),
        'events': recent_events,
    }


def timeline_markdown(timeline: dict[str, Any]) -> str:
    lines = [
        '# Research Quality Timeline',
        '',
        f"- Status: {'pass' if timeline.get('ok') else 'check'}",
        f"- CI artifacts shown: {timeline.get('event_count')} of {timeline.get('total_event_count')}",
        f"- Failures in window: {timeline.get('failure_count')}",
        f"- Regressions in window: {timeline.get('regression_count')}",
        f"- Latest: {timeline.get('latest_id') or 'none'}",
        '',
        '## Timeline',
        '',
        '| CI run | OK | Stack | Eval | Avg | Delta | Labels | Remediation | Failed | Rem Delta | Risks | Report |',
        '| --- | --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: | --- | --- |',
    ]
    for event in timeline.get('events', []) or []:
        if not isinstance(event, dict):
            continue
        labels = _labels_text(event.get('fixture_eval_labels'))
        risks = ', '.join(event.get('risk_flags') or []) or 'none'
        avg_delta = event.get('fixture_eval_average_delta')
        rem_delta = event.get('remediation_failed_delta')
        report = event.get('report_path') or ''
        lines.append(
            '| {id} | {ok} | {stack} | {eval_ok} | {avg} | {avg_delta} | {labels} | {rem_ok} | {rem_failed} | {rem_delta} | {risks} | [{report}]({report}) |'.format(
                id=event.get('id', ''),
                ok='yes' if event.get('ok') else 'no',
                stack='yes' if event.get('stack_ok') else 'no',
                eval_ok='yes' if event.get('fixture_eval_ok') else 'no',
                avg=event.get('fixture_eval_average_score') if event.get('fixture_eval_average_score') is not None else 'n/a',
                avg_delta=avg_delta if avg_delta is not None else 'n/a',
                labels=labels,
                rem_ok='yes' if event.get('remediation_ok') else 'no',
                rem_failed=event.get('remediation_failed', 0),
                rem_delta=rem_delta if rem_delta is not None else 'n/a',
                risks=risks,
                report=report,
            )
        )
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='Build a compact quality timeline from saved research CI artifacts.')
    parser.add_argument('--ci-root', type=Path, default=DEFAULT_CI_CHECK_ROOT)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    timeline = collect_quality_timeline(args.ci_root.expanduser().resolve(), limit=max(1, args.limit))
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(timeline_markdown(timeline), encoding='utf-8')
    json_path = output.with_suffix('.json')
    json_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.json:
        print(json.dumps(timeline, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({'ok': timeline.get('ok'), 'output': str(output), 'json': str(json_path)}, indent=2))
    return 0 if timeline.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
