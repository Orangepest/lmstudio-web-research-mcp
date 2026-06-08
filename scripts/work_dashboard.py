#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.compare_eval_runs import compare_eval_summaries
from scripts.quality_timeline import collect_quality_timeline


DEFAULT_PREFLIGHT_ROOT = ROOT / '.runtime' / 'work_preflights'
DEFAULT_EVAL_ROOT = ROOT / '.runtime' / 'evals'
DEFAULT_CI_CHECK_ROOT = ROOT / '.runtime' / 'ci_checks'
DEFAULT_WORK_LOOP_ROOT = ROOT / '.runtime' / 'work_loops'
DEFAULT_OUTPUT = ROOT / '.runtime' / 'work_dashboard.md'
DEFAULT_ACTION_HISTORY_ROOT = ROOT / '.runtime' / 'work_dashboard_actions'
DEFAULT_ACTION_DRILLDOWN_ROOT = ROOT / '.runtime' / 'work_dashboard_action_drilldowns'
DEFAULT_REMEDIATION_PLAN_OUTPUT = ROOT / '.runtime' / 'work_dashboard_remediation_plan.md'
DEFAULT_REMEDIATION_EVENT_ROOT = ROOT / '.runtime' / 'work_dashboard_remediation_events'


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _pid_alive(pid: object) -> bool | None:
    try:
        value = int(str(pid).strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _count_summary_text(items: object, *, limit: int = 2) -> str:
    if not isinstance(items, list) or not items:
        return 'none'
    parts = []
    for item in items[:limit]:
        if isinstance(item, dict):
            parts.append(f"{item.get('name', 'unknown')}:{item.get('count', 0)}")
    if len(items) > limit:
        parts.append(f"+{len(items) - limit} more")
    return ', '.join(parts) or 'none'


def _slug_text(value: object, *, limit: int = 96) -> str:
    text = str(value or 'unknown').lower()
    slug = ''.join(char if char.isalnum() else '-' for char in text).strip('-')
    while '--' in slug:
        slug = slug.replace('--', '-')
    return (slug or 'unknown')[:limit].strip('-') or 'unknown'


def _dashboard_action(
    *,
    severity: str,
    category: str,
    item_id: object,
    title: str,
    summary: str,
    status: str = 'open',
    detail: str = '',
    command: str | None = None,
    apply_command: str | None = None,
    report_path: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    subject = str(item_id or 'unknown')
    normalized_title = ''.join(char if char.isalnum() else '-' for char in title.lower()).strip('-')[:50]
    return {
        'id': f'{category}:{subject}:{normalized_title}',
        'severity': severity,
        'category': category,
        'title': title,
        'subject_id': subject,
        'status': status,
        'summary': summary,
        'detail': detail,
        'command': command,
        'apply_command': apply_command,
        'report_path': report_path,
        'details': details or {},
    }


def _action_history_item(action: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': action.get('id'),
        'severity': action.get('severity'),
        'category': action.get('category'),
        'title': action.get('title'),
        'subject_id': action.get('subject_id'),
        'status': action.get('status'),
        'summary': action.get('summary'),
        'report_path': action.get('report_path'),
    }


def load_latest_action_snapshot(root: Path) -> dict[str, Any]:
    for path in sorted(root.glob('*.json'), reverse=True):
        payload = _load_json(path)
        if isinstance(payload.get('actions'), list):
            payload.setdefault('path', str(path))
            return payload
    return {}


def load_action_snapshots(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    snapshots = []
    for path in sorted(root.glob('*.json'), reverse=True):
        payload = _load_json(path)
        if isinstance(payload.get('actions'), list):
            payload.setdefault('path', str(path))
            snapshots.append(payload)
        if len(snapshots) >= limit:
            break
    return snapshots


def build_action_history(
    actions: list[dict[str, Any]],
    previous_actions: list[dict[str, Any]] | None = None,
    *,
    previous_snapshot_path: str | None = None,
    prior_snapshots: list[dict[str, Any]] | None = None,
    suppress_after_seen: int = 2,
) -> dict[str, Any]:
    previous_actions = previous_actions or []
    prior_snapshots = prior_snapshots or []
    current_by_id = {str(action.get('id')): action for action in actions if isinstance(action, dict) and action.get('id')}
    previous_by_id = {
        str(action.get('id')): action for action in previous_actions if isinstance(action, dict) and action.get('id')
    }
    current_ids = set(current_by_id)
    previous_ids = set(previous_by_id)
    new_ids = sorted(current_ids - previous_ids)
    recurring_ids = sorted(current_ids & previous_ids)
    resolved_ids = sorted(previous_ids - current_ids)
    age_by_id: dict[str, dict[str, Any]] = {}
    for action_id in current_ids:
        seen_count = 1
        first_seen_path = None
        last_seen_path = None
        for snapshot in reversed(prior_snapshots):
            snapshot_actions = snapshot.get('actions') if isinstance(snapshot.get('actions'), list) else []
            snapshot_ids = {str(action.get('id')) for action in snapshot_actions if isinstance(action, dict)}
            if action_id in snapshot_ids:
                seen_count += 1
                first_seen_path = first_seen_path or snapshot.get('path')
                last_seen_path = snapshot.get('path')
        age_by_id[action_id] = {
            'seen_snapshot_count': seen_count,
            'first_seen_snapshot_path': first_seen_path,
            'last_seen_snapshot_path': last_seen_path,
        }
    suppressed_ids = []
    for action_id, action in current_by_id.items():
        severity = str(action.get('severity') or '')
        if severity in {'low', 'info'} and age_by_id.get(action_id, {}).get('seen_snapshot_count', 1) >= suppress_after_seen:
            suppressed_ids.append(action_id)
    return {
        'has_previous': bool(previous_actions),
        'previous_snapshot_path': previous_snapshot_path,
        'previous_action_count': len(previous_by_id),
        'current_action_count': len(current_by_id),
        'new_action_count': len(new_ids),
        'recurring_action_count': len(recurring_ids),
        'resolved_action_count': len(resolved_ids),
        'suppressed_action_count': len(suppressed_ids),
        'suppressed_action_ids': sorted(suppressed_ids),
        'age_by_action_id': age_by_id,
        'new_actions': [_action_history_item(current_by_id[item_id]) for item_id in new_ids[:10]],
        'recurring_actions': [_action_history_item(current_by_id[item_id]) for item_id in recurring_ids[:10]],
        'resolved_actions': [_action_history_item(previous_by_id[item_id]) for item_id in resolved_ids[:10]],
        'suppressed_actions': [_action_history_item(current_by_id[item_id]) for item_id in sorted(suppressed_ids)[:10]],
    }


def write_action_snapshot(root: Path, dashboard: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = root / f'{created_at}.json'
    payload = {
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'dashboard_ok': bool(dashboard.get('ok')),
        'action_count': int(dashboard.get('action_count') or 0),
        'action_summary': dashboard.get('action_summary') if isinstance(dashboard.get('action_summary'), dict) else {},
        'actions': dashboard.get('actions') if isinstance(dashboard.get('actions'), list) else [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def load_remediation_execution_events(root: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    events = []
    for path in sorted(root.glob('*.json'), reverse=True):
        payload = _load_json(path)
        if payload.get('step_id') and payload.get('status'):
            payload.setdefault('path', str(path))
            events.append(payload)
        if len(events) >= limit:
            break
    return events


def write_remediation_execution_event(
    root: Path,
    *,
    step_id: str,
    status: str,
    dashboard: dict[str, Any],
    note: str = '',
) -> Path:
    if status not in {'previewed', 'applied', 'resolved'}:
        raise ValueError('status must be previewed, applied, or resolved.')
    root.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = root / f'{created_at}-{_slug_text(step_id, limit=72)}-{status}.json'
    plan = dashboard.get('remediation_plan') if isinstance(dashboard.get('remediation_plan'), dict) else {}
    steps = plan.get('steps') if isinstance(plan.get('steps'), list) else []
    step_context = next(
        (dict(step) for step in steps if isinstance(step, dict) and str(step.get('id') or '') == step_id),
        {},
    )
    payload = {
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'step_id': step_id,
        'status': status,
        'note': note,
        'step_context': step_context,
        'dashboard_output': dashboard.get('output'),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def _collect_detail_paths(value: object) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if isinstance(item, str) and ('path' in key_text or key_text.endswith('_file')):
                paths.append(item)
            else:
                paths.extend(_collect_detail_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.extend(_collect_detail_paths(item))
    return paths


def _action_related_paths(action: dict[str, Any]) -> list[str]:
    paths = []
    for key in ('report_path', 'command', 'apply_command'):
        value = action.get(key)
        if key == 'report_path' and value:
            paths.append(str(value))
    paths.extend(_collect_detail_paths(action.get('details') or {}))
    deduped = []
    seen = set()
    for path in paths:
        if path and path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def build_action_drilldown(action: dict[str, Any], dashboard: dict[str, Any]) -> dict[str, Any]:
    action_id = str(action.get('id') or 'unknown')
    history = dashboard.get('action_history') if isinstance(dashboard.get('action_history'), dict) else {}
    age_by_id = history.get('age_by_action_id') if isinstance(history.get('age_by_action_id'), dict) else {}
    age = age_by_id.get(action_id) if isinstance(age_by_id.get(action_id), dict) else {}
    next_commands = []
    if action.get('command'):
        next_commands.append({'kind': 'preview', 'command': action.get('command')})
    if action.get('apply_command'):
        next_commands.append({'kind': 'apply', 'command': action.get('apply_command')})
    if not next_commands and action.get('report_path'):
        next_commands.append({'kind': 'inspect', 'path': action.get('report_path')})
    recurrence = {
        'seen_snapshot_count': int(age.get('seen_snapshot_count') or 1),
        'first_seen_snapshot_path': age.get('first_seen_snapshot_path'),
        'last_seen_snapshot_path': age.get('last_seen_snapshot_path'),
        'is_new': action_id in {item.get('id') for item in history.get('new_actions', []) if isinstance(item, dict)},
        'is_recurring': action_id in {
            item.get('id') for item in history.get('recurring_actions', []) if isinstance(item, dict)
        },
    }
    return {
        'id': action_id,
        'severity': action.get('severity'),
        'category': action.get('category'),
        'status': action.get('status'),
        'title': action.get('title'),
        'summary': action.get('summary'),
        'detail': action.get('detail'),
        'subject_id': action.get('subject_id'),
        'related_paths': _action_related_paths(action),
        'next_commands': next_commands,
        'recurrence': recurrence,
        'details': action.get('details') if isinstance(action.get('details'), dict) else {},
    }


def action_drilldown_markdown(drilldown: dict[str, Any]) -> str:
    lines = [
        f"# Action Drilldown: {drilldown.get('title') or drilldown.get('id')}",
        '',
        f"- ID: `{drilldown.get('id')}`",
        f"- Severity: {drilldown.get('severity')}",
        f"- Category: {drilldown.get('category')}",
        f"- Status: {drilldown.get('status')}",
        f"- Subject: {drilldown.get('subject_id')}",
        f"- Summary: {drilldown.get('summary')}",
    ]
    if drilldown.get('detail'):
        lines.append(f"- Detail: {drilldown.get('detail')}")
    recurrence = drilldown.get('recurrence') if isinstance(drilldown.get('recurrence'), dict) else {}
    lines.extend(
        [
            '',
            '## Recurrence',
            '',
            f"- Seen snapshots: {recurrence.get('seen_snapshot_count', 1)}",
            f"- New: {recurrence.get('is_new')}",
            f"- Recurring: {recurrence.get('is_recurring')}",
        ]
    )
    if recurrence.get('first_seen_snapshot_path'):
        lines.append(f"- First seen: [{recurrence.get('first_seen_snapshot_path')}]({recurrence.get('first_seen_snapshot_path')})")
    if recurrence.get('last_seen_snapshot_path'):
        lines.append(f"- Last seen: [{recurrence.get('last_seen_snapshot_path')}]({recurrence.get('last_seen_snapshot_path')})")
    lines.extend(['', '## Next Commands', ''])
    commands = drilldown.get('next_commands') if isinstance(drilldown.get('next_commands'), list) else []
    if commands:
        for command in commands:
            if not isinstance(command, dict):
                continue
            if command.get('command'):
                lines.append(f"- {command.get('kind')}: `{command.get('command')}`")
            elif command.get('path'):
                lines.append(f"- {command.get('kind')}: [{command.get('path')}]({command.get('path')})")
    else:
        lines.append('- none')
    lines.extend(['', '## Related Paths', ''])
    paths = drilldown.get('related_paths') if isinstance(drilldown.get('related_paths'), list) else []
    if paths:
        for path in paths:
            lines.append(f"- [{path}]({path})")
    else:
        lines.append('- none')
    details = drilldown.get('details') if isinstance(drilldown.get('details'), dict) else {}
    if details:
        lines.extend(['', '## Details', '', '```json', json.dumps(details, ensure_ascii=False, indent=2), '```'])
    lines.append('')
    return '\n'.join(lines)


def _remediation_command_for_action(action: dict[str, Any]) -> dict[str, Any]:
    category = str(action.get('category') or '')
    title = str(action.get('title') or '')
    subject = str(action.get('subject_id') or '')
    if action.get('command') or action.get('apply_command'):
        return {
            'kind': 'cleanup',
            'preview_command': action.get('command'),
            'apply_command': action.get('apply_command'),
            'why': 'Action already provides a targeted preview/apply command.',
        }
    if category in {'preflight', 'preflight_eval'}:
        return {
            'kind': 'work_session',
            'preview_command': 'python scripts/work_session.py --profile careful --dry-run --probe-tools --eval --stop-on-fail',
            'apply_command': 'python scripts/work_session.py --profile careful --probe-tools --eval --stop-on-fail',
            'why': 'Refresh preflight, fixture eval, and dashboard together with the careful work profile.',
        }
    if category in {'eval', 'source_selection'}:
        return {
            'kind': 'eval',
            'preview_command': 'python scripts/run_research_eval.py --profile careful --limit 1',
            'apply_command': 'python scripts/run_research_eval.py --profile careful',
            'why': 'Re-run the research eval suite to confirm the regression and produce fresh scored artifacts.',
        }
    if category in {'remediation_benchmark', 'quality_timeline'}:
        return {
            'kind': 'ci',
            'preview_command': 'python scripts/research_ci_check.py --skip-probe --limit 1 --json',
            'apply_command': 'python scripts/research_ci_check.py --json',
            'why': 'Refresh fixture eval, remediation benchmark, stack probe, and quality timeline together.',
        }
    return {
        'kind': 'research_runtime',
        'preview_command': (
            'python scripts/research_mission_runtime.py '
            f'"status for dashboard action {shlex.quote(subject or title or category)}" --json'
        ),
        'apply_command': None,
        'why': 'Inspect background research runtime state before queueing more work.',
    }


def build_dashboard_remediation_plan(dashboard: dict[str, Any], *, limit: int = 12) -> dict[str, Any]:
    severity_rank = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
    kind_rank = {'cleanup': 0, 'ci': 1, 'eval': 2, 'work_session': 3, 'research_runtime': 4}
    actions = dashboard.get('visible_actions') if isinstance(dashboard.get('visible_actions'), list) else []
    candidates = [
        action for action in actions
        if isinstance(action, dict) and str(action.get('severity') or '') in {'high', 'medium'}
    ]
    steps = []
    seen_commands = set()
    for action in sorted(
        candidates,
        key=lambda item: (
            severity_rank.get(str(item.get('severity') or ''), 99),
            str(item.get('category') or ''),
            str(item.get('subject_id') or ''),
        ),
    ):
        command_plan = _remediation_command_for_action(action)
        dedupe_key = command_plan.get('apply_command') or command_plan.get('preview_command') or action.get('id')
        if dedupe_key in seen_commands:
            continue
        seen_commands.add(str(dedupe_key))
        drilldown = build_action_drilldown(action, dashboard)
        steps.append(
            {
                'rank': 0,
                'id': action.get('id'),
                'severity': action.get('severity'),
                'category': action.get('category'),
                'status': action.get('status'),
                'title': action.get('title'),
                'summary': action.get('summary'),
                'kind': command_plan.get('kind'),
                'preview_command': command_plan.get('preview_command'),
                'apply_command': command_plan.get('apply_command'),
                'why': command_plan.get('why'),
                'related_paths': drilldown.get('related_paths') or [],
                'seen_snapshot_count': (drilldown.get('recurrence') or {}).get('seen_snapshot_count', 1),
            }
        )
    steps.sort(
        key=lambda item: (
            severity_rank.get(str(item.get('severity') or ''), 99),
            kind_rank.get(str(item.get('kind') or ''), 99),
            -int(item.get('seen_snapshot_count') or 1),
            str(item.get('id') or ''),
        )
    )
    for index, step in enumerate(steps[:limit], start=1):
        step['rank'] = index
    steps = steps[:limit]
    return {
        'ok': not steps,
        'step_count': len(steps),
        'high_count': sum(1 for item in steps if item.get('severity') == 'high'),
        'medium_count': sum(1 for item in steps if item.get('severity') == 'medium'),
        'previewed_count': 0,
        'applied_count': 0,
        'resolved_count': 0,
        'steps': steps,
    }


def apply_remediation_execution_tracking(
    plan: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    events = events or []
    latest_by_step: dict[str, dict[str, Any]] = {}
    ordered_events = sorted(
        (event for event in events if isinstance(event, dict)),
        key=lambda item: str(item.get('created_at') or item.get('path') or ''),
    )
    for event in ordered_events:
        if not isinstance(event, dict):
            continue
        step_id = str(event.get('step_id') or '')
        if step_id:
            latest_by_step[step_id] = event
    tracked = dict(plan)
    steps = []
    for step in plan.get('steps', []) if isinstance(plan.get('steps'), list) else []:
        if not isinstance(step, dict):
            continue
        item = dict(step)
        event = latest_by_step.get(str(item.get('id') or ''))
        if event:
            status = event.get('status')
            stale = bool(status == 'applied' and int(item.get('seen_snapshot_count') or 1) > 1)
            if stale:
                status = 'stale_applied'
                item['severity'] = 'high'
                item['stale_after_apply'] = True
                item['why'] = (
                    f"{item.get('why') or 'Remediation was applied.'} "
                    'This step still recurs after apply; escalate to a stronger repair or inspect the related artifact.'
                )
            item['execution'] = {
                'status': status,
                'event_status': event.get('status'),
                'status_at': event.get('created_at'),
                'note': event.get('note') or '',
                'event_path': event.get('path'),
                'stale_after_apply': stale,
            }
        else:
            item['execution'] = {'status': 'pending'}
        steps.append(item)
    tracked['steps'] = steps
    tracked['execution_event_count'] = len(events)
    tracked['previewed_count'] = sum(1 for item in steps if (item.get('execution') or {}).get('status') == 'previewed')
    tracked['applied_count'] = sum(1 for item in steps if (item.get('execution') or {}).get('status') == 'applied')
    tracked['stale_applied_count'] = sum(
        1 for item in steps if (item.get('execution') or {}).get('status') == 'stale_applied'
    )
    tracked['resolved_count'] = sum(1 for item in steps if (item.get('execution') or {}).get('status') == 'resolved')
    tracked['pending_count'] = sum(1 for item in steps if (item.get('execution') or {}).get('status') == 'pending')
    tracked['ok'] = not steps or tracked['resolved_count'] == len(steps)
    tracked['high_count'] = sum(1 for item in steps if item.get('severity') == 'high')
    tracked['medium_count'] = sum(1 for item in steps if item.get('severity') == 'medium')
    return tracked


def remediation_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        '# Work Dashboard Remediation Plan',
        '',
        f"- Status: {'clear' if plan.get('ok') else 'action_needed'}",
        f"- Steps: {plan.get('step_count', 0)}",
        f"- High: {plan.get('high_count', 0)}",
        f"- Medium: {plan.get('medium_count', 0)}",
        f"- Pending: {plan.get('pending_count', 0)}",
        f"- Previewed: {plan.get('previewed_count', 0)}",
        f"- Applied: {plan.get('applied_count', 0)}",
        f"- Stale after apply: {plan.get('stale_applied_count', 0)}",
        f"- Resolved: {plan.get('resolved_count', 0)}",
        '',
        '| Rank | Severity | Kind | Execution | Action | Preview | Apply |',
        '| ---: | --- | --- | --- | --- | --- | --- |',
    ]
    steps = plan.get('steps') if isinstance(plan.get('steps'), list) else []
    if not steps:
        lines.append('| 0 | info | none | clear | No high/medium dashboard remediation steps. |  |  |')
    for step in steps:
        if not isinstance(step, dict):
            continue
        preview = f"`{step.get('preview_command')}`" if step.get('preview_command') else ''
        apply = f"`{step.get('apply_command')}`" if step.get('apply_command') else ''
        execution = step.get('execution') if isinstance(step.get('execution'), dict) else {}
        lines.append(
            '| {rank} | {severity} | {kind} | {execution} | {summary} | {preview} | {apply} |'.format(
                rank=step.get('rank', ''),
                severity=step.get('severity', ''),
                kind=step.get('kind', ''),
                execution=execution.get('status') or 'pending',
                summary=step.get('summary', ''),
                preview=preview,
                apply=apply,
            )
        )
    if steps:
        lines.extend(['', '## Notes', ''])
        for step in steps:
            if isinstance(step, dict):
                lines.append(f"- {step.get('rank')}. {step.get('why')}")
    lines.append('')
    return '\n'.join(lines)


def write_remediation_plan(path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_path = path.with_suffix('.json')
    path.write_text(remediation_plan_markdown(plan), encoding='utf-8')
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'markdown_path': str(path), 'json_path': str(json_path)}


def write_action_drilldown_exports(
    root: Path,
    dashboard: dict[str, Any],
    *,
    visible_only: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    actions_key = 'visible_actions' if visible_only else 'actions'
    actions = dashboard.get(actions_key) if isinstance(dashboard.get(actions_key), list) else []
    written = []
    for action in actions[:limit]:
        if not isinstance(action, dict):
            continue
        drilldown = build_action_drilldown(action, dashboard)
        action_dir = root / _slug_text(drilldown.get('id'))
        action_dir.mkdir(parents=True, exist_ok=True)
        json_path = action_dir / 'action.json'
        md_path = action_dir / 'action.md'
        json_path.write_text(json.dumps(drilldown, ensure_ascii=False, indent=2), encoding='utf-8')
        md_path.write_text(action_drilldown_markdown(drilldown), encoding='utf-8')
        written.append(
            {
                'id': drilldown.get('id'),
                'severity': drilldown.get('severity'),
                'category': drilldown.get('category'),
                'status': drilldown.get('status'),
                'markdown_path': str(md_path),
                'json_path': str(json_path),
            }
        )
    index = {
        'ok': True,
        'root': str(root),
        'visible_only': visible_only,
        'action_count': len(written),
        'actions': written,
    }
    index_json = root / 'index.json'
    index_md = root / 'index.md'
    index_json.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Work Dashboard Action Drilldowns', '', f"- Actions: {len(written)}", '', '| Severity | Category | Status | Action | Drilldown |', '| --- | --- | --- | --- | --- |']
    for item in written:
        lines.append(
            '| {severity} | {category} | {status} | `{id}` | [{path}]({path}) |'.format(
                severity=item.get('severity') or '',
                category=item.get('category') or '',
                status=item.get('status') or '',
                id=item.get('id') or '',
                path=item.get('markdown_path') or '',
            )
        )
    lines.append('')
    index_md.write_text('\n'.join(lines), encoding='utf-8')
    index['index_json_path'] = str(index_json)
    index['index_markdown_path'] = str(index_md)
    return index


def _preflight_eval_summary(payload: dict[str, Any]) -> dict[str, Any]:
    eval_smoke = payload.get('eval_smoke') if isinstance(payload.get('eval_smoke'), dict) else {}
    summary = eval_smoke.get('summary') if isinstance(eval_smoke.get('summary'), dict) else {}
    return {
        'eval_smoke_enabled': bool(eval_smoke),
        'eval_smoke_returncode': eval_smoke.get('returncode') if eval_smoke else None,
        'eval_smoke_mode': eval_smoke.get('mode') if eval_smoke else None,
        'eval_smoke_summary_path': eval_smoke.get('summary_path') if eval_smoke else None,
        'eval_smoke_summary_json_path': eval_smoke.get('summary_json_path') if eval_smoke else None,
        'eval_smoke_tasks_path': eval_smoke.get('tasks_path') if eval_smoke else None,
        'eval_smoke_fixture_path': eval_smoke.get('fixture_path') if eval_smoke else None,
        'eval_smoke_task_count': int(summary.get('task_count') or 0) if summary else 0,
        'eval_smoke_average_score': summary.get('average_score') if summary else None,
        'eval_smoke_label_counts': summary.get('labels') if isinstance(summary.get('labels'), dict) else {},
        'eval_smoke_score_cap_count': int(summary.get('score_cap_count') or 0) if summary else 0,
        'eval_smoke_score_caps': summary.get('score_caps') if isinstance(summary.get('score_caps'), list) else [],
        'eval_smoke_required_check_failure_count': int(summary.get('required_check_failure_count') or 0) if summary else 0,
        'eval_smoke_failed_required_checks': (
            summary.get('failed_required_checks') if isinstance(summary.get('failed_required_checks'), list) else []
        ),
        'eval_smoke_buried_strong_selected_count': int(summary.get('buried_strong_selected_count') or 0) if summary else 0,
        'eval_smoke_selected_low_value_source_count': int(summary.get('selected_low_value_source_count') or 0)
        if summary
        else 0,
        'eval_smoke_planned_low_value_source_count': int(summary.get('planned_low_value_source_count') or 0)
        if summary
        else 0,
        'eval_smoke_contradiction_resolution_search_count': int(
            summary.get('contradiction_resolution_search_count') or 0
        )
        if summary
        else 0,
    }


def _work_loop_guidance(item: dict[str, Any]) -> dict[str, Any]:
    issues = []
    if item.get('stale'):
        issues.append('stale')
    if item.get('reviewed') and not item.get('ok'):
        issues.append('reviewed_failed')
    elif not item.get('ok') and not item.get('in_progress'):
        issues.append('failed')
    if item.get('reported_in_progress') and not item.get('in_progress'):
        issues.append('not_running')

    loop_id = str(item.get('id') or '')
    quoted_loop_id = shlex.quote(loop_id)
    cleanup_eligible = bool(item.get('stale') and item.get('pid') is not None)
    cleanup_blockers = []
    if item.get('stale') and item.get('pid') is None:
        cleanup_blockers.append('missing_pid_requires_include_legacy_missing_pid')
    cleanup_preview_command = None
    cleanup_apply_command = None
    review_preview_command = None
    review_apply_command = None
    if item.get('stale') and loop_id:
        cleanup_preview_command = f'python scripts/cleanup_work_loops.py --loop-id {quoted_loop_id} --json'
        cleanup_apply_command = f'python scripts/cleanup_work_loops.py --apply --loop-id {quoted_loop_id} --json'
        if item.get('pid') is None:
            cleanup_eligible = False
            cleanup_apply_command += ' --include-legacy-missing-pid'
    failed_unacknowledged = bool(
        not item.get('ok') and not item.get('in_progress') and not item.get('stale') and not item.get('reviewed')
    )
    if failed_unacknowledged and loop_id:
        review_preview_command = f'python scripts/cleanup_work_loops.py --review-failed --loop-id {quoted_loop_id} --json'
        review_apply_command = f'python scripts/cleanup_work_loops.py --review-failed --apply --loop-id {quoted_loop_id} --json'

    inspect_targets = []
    if item.get('report_path'):
        inspect_targets.append(str(item.get('report_path')))
    if item.get('events_path'):
        inspect_targets.append(str(item.get('events_path')))

    return {
        'issue_count': len(set(issues)),
        'issue_codes': sorted(set(issues)),
        'cleanup_eligible': cleanup_eligible,
        'cleanup_blockers': cleanup_blockers,
        'cleanup_preview_command': cleanup_preview_command,
        'cleanup_apply_command': cleanup_apply_command,
        'failed_unacknowledged': failed_unacknowledged,
        'review_preview_command': review_preview_command,
        'review_apply_command': review_apply_command,
        'inspect_targets': inspect_targets,
    }


def collect_preflights(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    items = []
    for path in sorted(root.glob('*/preflight.json'), reverse=True):
        payload = _load_json(path)
        if not payload:
            continue
        risk = payload.get('risk') if isinstance(payload.get('risk'), dict) else {}
        status = payload.get('status') if isinstance(payload.get('status'), dict) else {}
        runs = status.get('runs') if isinstance(status.get('runs'), dict) else {}
        eval_summary = _preflight_eval_summary(payload)
        items.append(
            {
                'id': path.parent.name,
                'path': str(path),
                'report_path': str(path.with_name('preflight.md')),
                'ok': bool(payload.get('ok')),
                'completed_at': payload.get('completed_at'),
                'dry_run': bool(payload.get('dry_run')),
                'probe_tools': bool(payload.get('probe_tools')),
                'risk_count': int(risk.get('risk_count') or 0),
                'high_count': int(risk.get('high_count') or 0),
                'medium_count': int(risk.get('medium_count') or 0),
                'total_runs': int(runs.get('total_runs') or 0),
                'latest_budget_totals': runs.get('latest_budget_totals') if isinstance(runs.get('latest_budget_totals'), dict) else {},
                **eval_summary,
            }
        )
        if len(items) >= limit:
            break
    return items


def collect_evals(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    summaries = []
    for path in sorted(root.glob('*/summary.json'), reverse=True):
        summary = _load_json(path)
        if not summary:
            continue
        summary.setdefault('id', path.parent.name)
        summary.setdefault('path', str(path))
        summaries.append(summary)
    items = []
    for index, summary in enumerate(summaries):
        path = Path(str(summary.get('path')))
        thresholds = summary.get('thresholds') if isinstance(summary.get('thresholds'), dict) else {}
        trend = {}
        if index + 1 < len(summaries):
            comparison = compare_eval_summaries(summaries[index + 1], summary)
            if comparison.get('ok'):
                trend = comparison.get('delta') if isinstance(comparison.get('delta'), dict) else {}
        items.append(
            {
                'id': path.parent.name,
                'path': str(path),
                'report_path': str(path.with_name('summary.md')),
                'ok': bool(summary.get('ok')) and (not thresholds or bool(thresholds.get('ok'))),
                'completed_at': summary.get('completed_at'),
                'task_count': int(summary.get('task_count') or 0),
                'average_score': summary.get('average_score'),
                'labels': summary.get('labels') if isinstance(summary.get('labels'), dict) else {},
                'threshold_failure_count': int(thresholds.get('failure_count') or 0),
                'trend_average_score_delta': trend.get('average_score'),
                'trend_regression_count': trend.get('regression_count'),
                'trend_improvement_count': trend.get('improvement_count'),
                'trend_buried_strong_selected_delta': trend.get('buried_strong_selected_delta'),
                'trend_selected_low_value_source_delta': trend.get('selected_low_value_source_delta'),
                'trend_planned_low_value_source_delta': trend.get('planned_low_value_source_delta'),
            }
        )
        if len(items) >= limit:
            break
    return items


def collect_remediation_benchmarks(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    summaries = []
    for path in sorted(root.glob('*/remediation_learning_benchmark.json'), reverse=True):
        summary = _load_json(path)
        if not summary:
            continue
        summary.setdefault('id', path.parent.name)
        summary.setdefault('path', str(path))
        summaries.append(summary)

    items = []
    for index, summary in enumerate(summaries):
        path = Path(str(summary.get('path')))
        records = summary.get('records') if isinstance(summary.get('records'), list) else []
        failed_records = [item for item in records if isinstance(item, dict) and not item.get('ok')]
        failed_scenarios = [
            str(item.get('id') or 'unknown') for item in failed_records if isinstance(item, dict)
        ]
        strategy_failure_count = sum(
            int(item.get('failure_count') or 0) for item in records if isinstance(item, dict)
        )
        passed = int(summary.get('passed') or 0)
        failed = int(summary.get('failed') or len(failed_records))
        trend_passed_delta = None
        trend_failed_delta = None
        if index + 1 < len(summaries):
            previous = summaries[index + 1]
            trend_passed_delta = passed - int(previous.get('passed') or 0)
            trend_failed_delta = failed - int(previous.get('failed') or 0)
        ci_report = path.with_name('ci_check.md')
        report_path = str(ci_report if ci_report.exists() else path)
        items.append(
            {
                'id': path.parent.name,
                'path': str(path),
                'report_path': report_path,
                'ok': bool(summary.get('ok')) and failed == 0 and strategy_failure_count == 0,
                'scenario_count': int(summary.get('scenario_count') or len(records)),
                'passed': passed,
                'failed': failed,
                'strategy_failure_count': strategy_failure_count,
                'failed_scenarios': failed_scenarios,
                'trend_passed_delta': trend_passed_delta,
                'trend_failed_delta': trend_failed_delta,
            }
        )
        if len(items) >= limit:
            break
    return items


def collect_work_loops(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    items = []
    for path in sorted(root.glob('*/work_loop.json'), reverse=True):
        payload = _load_json(path)
        if not payload:
            continue
        profile = payload.get('profile') if isinstance(payload.get('profile'), dict) else {}
        reported_in_progress = bool(payload.get('in_progress'))
        pid_alive = _pid_alive(payload.get('pid'))
        in_progress = reported_in_progress and bool(pid_alive)
        review = payload.get('review') if isinstance(payload.get('review'), dict) else {}
        item = {
            'id': path.parent.name,
            'path': str(path),
            'report_path': str(path.with_name('work_loop.md')),
            'events_path': str(path.with_name('events.jsonl')),
            'ok': bool(payload.get('ok')),
            'in_progress': in_progress,
            'reported_in_progress': reported_in_progress,
            'stale': reported_in_progress and not in_progress,
            'pid': payload.get('pid'),
            'pid_alive': pid_alive,
            'updated_at': payload.get('updated_at'),
            'completed_at': payload.get('completed_at'),
            'reviewed': bool(review.get('reviewed')),
            'reviewed_at': review.get('reviewed_at'),
            'review_note': review.get('note') or '',
            'profile': profile.get('name') or 'unknown',
            'cycle_count': int(payload.get('cycle_count') or payload.get('iteration_count') or 0),
            'failed_cycle_count': int(payload.get('failed_cycle_count') or payload.get('failed_iteration_count') or 0),
            'consecutive_failure_count': int(payload.get('consecutive_failure_count') or 0),
            'stop_reason': payload.get('stop_reason') or 'unknown',
        }
        item.update(_work_loop_guidance(item))
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _risk_actions(preflight: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    if not preflight.get('ok') and int(preflight.get('risk_count') or 0) == 0:
        actions.append(
            _dashboard_action(
                severity='high',
                category='preflight',
                item_id=preflight.get('id'),
                title='Preflight failed',
                summary='Preflight did not pass.',
                detail='Open the preflight report to inspect the failed readiness check.',
                report_path=preflight.get('report_path'),
            )
        )
    if int(preflight.get('high_count') or 0) > 0:
        actions.append(
            _dashboard_action(
                severity='high',
                category='preflight',
                item_id=preflight.get('id'),
                title='High-risk preflight issues',
                summary=f"{preflight.get('high_count')} high-risk preflight issue(s).",
                detail='Open the preflight report and resolve stack/tool/config failures before relying on the session.',
                report_path=preflight.get('report_path'),
            )
        )
    elif int(preflight.get('medium_count') or 0) > 0:
        actions.append(
            _dashboard_action(
                severity='medium',
                category='preflight',
                item_id=preflight.get('id'),
                title='Medium-risk preflight issues',
                summary=f"{preflight.get('medium_count')} medium-risk preflight issue(s).",
                detail='Review preflight warnings before treating this as a clean work-session baseline.',
                report_path=preflight.get('report_path'),
            )
        )
    blocked = 0
    budget = preflight.get('latest_budget_totals') if isinstance(preflight.get('latest_budget_totals'), dict) else {}
    blocked = int(budget.get('blocked_source_count') or 0)
    if blocked:
        actions.append(
            _dashboard_action(
                severity='low',
                category='preflight',
                item_id=preflight.get('id'),
                title='Blocked sources in latest run',
                summary=f'{blocked} blocked source(s) in latest run totals.',
                detail='Inspect blocked-source handoff links if a recent run looked thin.',
                report_path=preflight.get('report_path'),
            )
        )
    if preflight.get('eval_smoke_enabled') and preflight.get('eval_smoke_returncode') not in {0, None}:
        actions.append(
            _dashboard_action(
                severity='high',
                category='preflight_eval',
                item_id=preflight.get('id'),
                title='Eval smoke failed',
                summary=f"Eval smoke returned {preflight.get('eval_smoke_returncode')}.",
                detail='Open the eval-smoke summary or preflight report before trusting this session.',
                report_path=preflight.get('eval_smoke_summary_path') or preflight.get('report_path'),
            )
        )
    if int(preflight.get('eval_smoke_score_cap_count') or 0) > 0:
        actions.append(
            _dashboard_action(
                severity='medium',
                category='preflight_eval',
                item_id=preflight.get('id'),
                title='Eval smoke score caps',
                summary=f"{preflight.get('eval_smoke_score_cap_count')} eval-smoke score cap(s).",
                detail=_count_summary_text(preflight.get('eval_smoke_score_caps')),
                report_path=preflight.get('eval_smoke_summary_path') or preflight.get('report_path'),
            )
        )
    selected_low = int(preflight.get('eval_smoke_selected_low_value_source_count') or 0)
    planned_low = int(preflight.get('eval_smoke_planned_low_value_source_count') or 0)
    if selected_low or planned_low:
        actions.append(
            _dashboard_action(
                severity='medium',
                category='source_selection',
                item_id=preflight.get('id'),
                title='Eval smoke low-value source selection',
                summary=f'{selected_low} selected / {planned_low} planned low-value source(s).',
                detail='Review source-selection behavior before trusting noisy search-result handling.',
                report_path=preflight.get('eval_smoke_summary_path') or preflight.get('report_path'),
            )
        )
    return actions


def _eval_actions(item: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    labels = item.get('labels') if isinstance(item.get('labels'), dict) else {}
    fail_count = int(labels.get('fail') or 0)
    threshold_failures = int(item.get('threshold_failure_count') or 0)
    if not item.get('ok') or threshold_failures:
        actions.append(
            _dashboard_action(
                severity='high',
                category='eval',
                item_id=item.get('id'),
                title='Eval threshold failure',
                summary=f'{threshold_failures} eval threshold failure(s).',
                detail='Open the eval summary before treating the current research behavior as stable.',
                report_path=item.get('report_path'),
            )
        )
    if fail_count:
        actions.append(
            _dashboard_action(
                severity='high',
                category='eval',
                item_id=item.get('id'),
                title='Failing eval tasks',
                summary=f'{fail_count} failing eval task(s).',
                detail='Investigate failing eval records and weakest checks.',
                report_path=item.get('report_path'),
            )
        )
    regressions = int(item.get('trend_regression_count') or 0)
    if regressions:
        actions.append(
            _dashboard_action(
                severity='medium',
                category='eval',
                item_id=item.get('id'),
                title='Eval regressions',
                summary=f'{regressions} eval regression(s) versus prior run.',
                detail=f"Average score delta: {item.get('trend_average_score_delta')}",
                report_path=item.get('report_path'),
            )
        )
    low_value_delta = item.get('trend_selected_low_value_source_delta')
    if low_value_delta is not None and int(low_value_delta or 0) > 0:
        actions.append(
            _dashboard_action(
                severity='medium',
                category='source_selection',
                item_id=item.get('id'),
                title='Low-value source selection regression',
                summary=f'+{int(low_value_delta)} low-value source selection delta.',
                detail='Review source reranking and planned-read behavior.',
                report_path=item.get('report_path'),
            )
        )
    buried_delta = item.get('trend_buried_strong_selected_delta')
    if buried_delta is not None and int(buried_delta or 0) < 0:
        actions.append(
            _dashboard_action(
                severity='low',
                category='source_selection',
                item_id=item.get('id'),
                title='Buried strong source regression',
                summary=f'{int(buried_delta)} buried-strong source selection delta.',
                detail='Review whether source selection is missing strong results below the top ranks.',
                report_path=item.get('report_path'),
            )
        )
    return actions


def _remediation_benchmark_actions(item: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    failed = int(item.get('failed') or 0)
    strategy_failures = int(item.get('strategy_failure_count') or 0)
    if not item.get('ok') or failed or strategy_failures:
        actions.append(
            _dashboard_action(
                severity='high',
                category='remediation_benchmark',
                item_id=item.get('id'),
                title='Remediation benchmark failure',
                summary=f'{failed} remediation benchmark scenario(s) failed.',
                detail=f'{strategy_failures} strategy-ranking failure(s) recorded.',
                report_path=item.get('report_path'),
            )
        )
    failed_delta = item.get('trend_failed_delta')
    if failed_delta is not None and int(failed_delta or 0) > 0:
        actions.append(
            _dashboard_action(
                severity='medium',
                category='remediation_benchmark',
                item_id=item.get('id'),
                title='Remediation benchmark regression',
                summary=f'+{int(failed_delta)} failed remediation benchmark scenario(s) versus prior run.',
                detail='Compare the latest CI benchmark artifact against the previous run before relying on learned strategy ranking.',
                report_path=item.get('report_path'),
            )
        )
    return actions


def _quality_timeline_actions(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    events = timeline.get('events') if isinstance(timeline.get('events'), list) else []

    def add_issue(
        event: dict[str, Any],
        *,
        issue_key: str,
        severity: str,
        title: str,
        summary: str,
        detail: str,
        report_path: object,
        extra_details: dict[str, Any] | None = None,
    ) -> None:
        flags = set(event.get('risk_flags') or [])
        bucket = grouped.setdefault(
            issue_key,
            {
                'severity': severity,
                'title': title,
                'summary': summary,
                'detail': detail,
                'report_path': str(report_path or ''),
                'event_ids': [],
                'risk_flags': set(),
                'details': {},
            },
        )
        if bucket.get('severity') != 'high' and severity == 'high':
            bucket['severity'] = severity
        bucket['event_ids'].append(str(event.get('id') or 'unknown'))
        bucket['risk_flags'].update(flags)
        bucket['details'].update(extra_details or {})
        if report_path and not bucket.get('report_path'):
            bucket['report_path'] = str(report_path)

    for event in events[:5]:
        if not isinstance(event, dict):
            continue
        flags = set(event.get('risk_flags') or [])
        report_path = event.get('report_path') or timeline.get('output')
        if 'stack_failed' in flags or event.get('stack_ok') is False:
            add_issue(
                event,
                issue_key='stack-check-failed',
                severity='high',
                title='CI stack check failed',
                summary='Quality timeline reports a failing stack check.',
                detail='Open the CI report and repair prompt/docs/config/search/tool probe issues before trusting research automation.',
                report_path=report_path,
            )
        if 'fixture_eval_failed' in flags or 'eval_threshold_failures' in flags or int(event.get('fixture_eval_threshold_failure_count') or 0) > 0:
            add_issue(
                event,
                issue_key='fixture-eval-quality-failure',
                severity='high',
                title='Fixture eval quality failure',
                summary=f"{event.get('fixture_eval_threshold_failure_count') or 0} fixture eval threshold failure(s).",
                detail='Review deterministic fixture eval output from the CI check.',
                report_path=event.get('fixture_eval_summary_md_path') or report_path,
                extra_details={'threshold_failure_count': int(event.get('fixture_eval_threshold_failure_count') or 0)},
            )
        if 'remediation_failed' in flags or 'remediation_scenarios_failed' in flags:
            add_issue(
                event,
                issue_key='remediation-learning-failure',
                severity='high',
                title='Remediation learning failure',
                summary=f"{event.get('remediation_failed') or 0} remediation scenario(s) failed.",
                detail='Review remediation learning benchmark output from the CI check.',
                report_path=event.get('remediation_path') or report_path,
                extra_details={'failed_scenarios': event.get('remediation_failed_scenarios') or []},
            )
        if 'eval_score_drop' in flags:
            add_issue(
                event,
                issue_key='fixture-eval-score-dropped',
                severity='medium',
                title='Fixture eval score dropped',
                summary=f"Fixture eval average delta: {event.get('fixture_eval_average_delta')}.",
                detail='Compare the latest CI fixture eval against the previous run.',
                report_path=event.get('fixture_eval_summary_md_path') or report_path,
                extra_details={'average_delta': event.get('fixture_eval_average_delta')},
            )
        if 'remediation_regression' in flags:
            add_issue(
                event,
                issue_key='remediation-benchmark-regression',
                severity='medium',
                title='Remediation benchmark regression',
                summary=f"Remediation failed-scenario delta: {event.get('remediation_failed_delta')}.",
                detail='Compare the latest remediation benchmark against the previous run.',
                report_path=event.get('remediation_path') or report_path,
                extra_details={'failed_delta': event.get('remediation_failed_delta')},
            )
        if 'search_provider_failures' in flags:
            add_issue(
                event,
                issue_key='search-provider-failures',
                severity='medium',
                title='Search provider failures in CI',
                summary=f"{event.get('stack_search_failure_count') or 0} recent search-provider failure(s).",
                detail='Inspect search provider order, SearXNG health, and fallback attempts.',
                report_path=report_path,
                extra_details={'search_failure_count': int(event.get('stack_search_failure_count') or 0)},
            )
    actions = []
    for issue_key, issue in grouped.items():
        event_ids = issue.get('event_ids') if isinstance(issue.get('event_ids'), list) else []
        occurrence_count = len(event_ids)
        summary = str(issue.get('summary') or '')
        if occurrence_count > 1:
            summary = f"{summary} Seen in {occurrence_count} recent CI run(s): {', '.join(event_ids[:3])}."
        details = issue.get('details') if isinstance(issue.get('details'), dict) else {}
        details.update(
            {
                'occurrence_count': occurrence_count,
                'event_ids': event_ids,
                'risk_flags': sorted(issue.get('risk_flags') or []),
            }
        )
        actions.append(
            _dashboard_action(
                severity=str(issue.get('severity') or 'medium'),
                category='quality_timeline',
                item_id=issue_key,
                title=str(issue.get('title') or issue_key),
                summary=summary,
                status='recurring' if occurrence_count > 1 else 'open',
                detail=str(issue.get('detail') or ''),
                report_path=str(issue.get('report_path') or ''),
                details=details,
            )
        )
    return actions


def _apply_action_history_acknowledgements(
    actions: list[dict[str, Any]],
    action_history: dict[str, Any],
    *,
    acknowledge_after_seen: int = 2,
) -> list[dict[str, Any]]:
    age_by_id = action_history.get('age_by_action_id') if isinstance(action_history.get('age_by_action_id'), dict) else {}
    annotated = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        item = dict(action)
        action_id = str(item.get('id') or '')
        age = age_by_id.get(action_id) if isinstance(age_by_id.get(action_id), dict) else {}
        seen_count = int(age.get('seen_snapshot_count') or 1)
        if item.get('category') == 'quality_timeline' and seen_count >= acknowledge_after_seen:
            item['status'] = 'acknowledged_recurring'
            details = item.get('details') if isinstance(item.get('details'), dict) else {}
            details = dict(details)
            details['seen_snapshot_count'] = seen_count
            item['details'] = details
        annotated.append(item)
    return annotated


def _work_loop_actions(item: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    if item.get('stale'):
        severity = 'medium' if item.get('cleanup_eligible') else 'low'
        command = item.get('cleanup_preview_command') or item.get('cleanup_apply_command')
        detail = 'Preview stale cleanup before applying.'
        if item.get('cleanup_blockers'):
            detail = f"Blocked by {', '.join(item.get('cleanup_blockers') or [])}."
        actions.append(
            _dashboard_action(
                severity=severity,
                category='work_loop',
                item_id=item.get('id'),
                title='Stale work-loop artifact',
                summary='Stale work-loop artifact.',
                status='blocked' if item.get('cleanup_blockers') else 'open',
                detail=detail,
                command=command,
                apply_command=item.get('cleanup_apply_command'),
                report_path=item.get('report_path'),
                details={'cleanup_blockers': item.get('cleanup_blockers') or []},
            )
        )
    if item.get('failed_unacknowledged'):
        actions.append(
            _dashboard_action(
                severity='high',
                category='work_loop',
                item_id=item.get('id'),
                title='Failed work-loop artifact needs review',
                summary='Failed work-loop artifact needs review.',
                detail='Inspect report/events, then mark reviewed if the failure is understood.',
                command=item.get('review_preview_command'),
                apply_command=item.get('review_apply_command'),
                report_path=item.get('report_path'),
            )
        )
    if item.get('reviewed') and not item.get('ok') and not item.get('stale'):
        actions.append(
            _dashboard_action(
                severity='info',
                category='work_loop',
                item_id=item.get('id'),
                title='Reviewed failed work loop',
                summary='Reviewed failed work loop.',
                status='acknowledged',
                detail=item.get('review_note') or 'Acknowledged failure remains preserved.',
                report_path=item.get('report_path'),
            )
        )
    return actions


def build_dashboard_action_summary(
    preflights: list[dict[str, Any]],
    evals: list[dict[str, Any]],
    work_loops: list[dict[str, Any]],
    remediation_benchmarks: list[dict[str, Any]] | None = None,
    quality_timeline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    remediation_benchmarks = remediation_benchmarks or []
    quality_timeline = quality_timeline or {}
    actions = []
    for item in work_loops[:10]:
        actions.extend(_work_loop_actions(item))
    for item in preflights[:5]:
        actions.extend(_risk_actions(item))
    for item in evals[:5]:
        actions.extend(_eval_actions(item))
    for item in remediation_benchmarks[:5]:
        actions.extend(_remediation_benchmark_actions(item))
    if quality_timeline:
        actions.extend(_quality_timeline_actions(quality_timeline))
    severity_rank = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
    actions.sort(key=lambda item: (severity_rank.get(str(item.get('severity')), 99), str(item.get('category')), str(item.get('item_id'))))
    counts: dict[str, int] = {}
    for item in actions:
        severity = str(item.get('severity') or 'unknown')
        counts[severity] = counts.get(severity, 0) + 1
    return {
        'action_count': len(actions),
        'high_count': counts.get('high', 0),
        'medium_count': counts.get('medium', 0),
        'low_count': counts.get('low', 0),
        'info_count': counts.get('info', 0),
        'actions': actions,
    }


def build_dashboard(
    preflights: list[dict[str, Any]],
    evals: list[dict[str, Any]],
    work_loops: list[dict[str, Any]] | None = None,
    *,
    remediation_benchmarks: list[dict[str, Any]] | None = None,
    quality_timeline: dict[str, Any] | None = None,
    previous_actions: list[dict[str, Any]] | None = None,
    previous_snapshot_path: str | None = None,
    prior_action_snapshots: list[dict[str, Any]] | None = None,
    remediation_execution_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    work_loops = work_loops or []
    remediation_benchmarks = remediation_benchmarks or []
    quality_timeline = quality_timeline or {}
    action_summary = build_dashboard_action_summary(preflights, evals, work_loops, remediation_benchmarks, quality_timeline)
    action_history = build_action_history(
        action_summary['actions'],
        previous_actions,
        previous_snapshot_path=previous_snapshot_path,
        prior_snapshots=prior_action_snapshots,
    )
    annotated_actions = _apply_action_history_acknowledgements(action_summary['actions'], action_history)
    action_summary = dict(action_summary)
    action_summary['actions'] = annotated_actions
    suppressed_action_ids = set(action_history.get('suppressed_action_ids') or [])
    visible_actions = [action for action in annotated_actions if action.get('id') not in suppressed_action_ids]
    remediation_plan = apply_remediation_execution_tracking(
        build_dashboard_remediation_plan({'visible_actions': visible_actions, 'action_history': action_history}),
        remediation_execution_events,
    )
    return {
        'ok': (
            all(item.get('ok') for item in preflights[:3])
            and all(item.get('ok') for item in evals[:3])
            and all(
                item.get('ok') or item.get('in_progress') or (item.get('reviewed') and not item.get('stale'))
                for item in work_loops[:3]
            )
            and all(item.get('ok') for item in remediation_benchmarks[:3])
            and bool(quality_timeline.get('ok', True))
        ),
        'preflight_count': len(preflights),
        'eval_count': len(evals),
        'remediation_benchmark_count': len(remediation_benchmarks),
        'quality_timeline_event_count': int(quality_timeline.get('event_count') or 0) if quality_timeline else 0,
        'quality_timeline_failure_count': int(quality_timeline.get('failure_count') or 0) if quality_timeline else 0,
        'quality_timeline_regression_count': int(quality_timeline.get('regression_count') or 0) if quality_timeline else 0,
        'work_loop_count': len(work_loops),
        'latest_preflight_ok': preflights[0]['ok'] if preflights else None,
        'latest_eval_ok': evals[0]['ok'] if evals else None,
        'latest_remediation_benchmark_ok': remediation_benchmarks[0]['ok'] if remediation_benchmarks else None,
        'latest_quality_timeline_ok': quality_timeline.get('ok') if quality_timeline else None,
        'latest_work_loop_ok': work_loops[0]['ok'] if work_loops else None,
        'preflight_failures': sum(1 for item in preflights if not item.get('ok')),
        'eval_failures': sum(1 for item in evals if not item.get('ok')),
        'remediation_benchmark_failures': sum(1 for item in remediation_benchmarks if not item.get('ok')),
        'work_loop_failures': sum(1 for item in work_loops if item.get('failed_unacknowledged')),
        'unreviewed_work_loop_failures': sum(1 for item in work_loops if item.get('failed_unacknowledged')),
        'reviewed_work_loop_failures': sum(1 for item in work_loops if item.get('reviewed') and not item.get('ok')),
        'stale_work_loop_count': sum(1 for item in work_loops if item.get('stale')),
        'work_loop_issue_count': sum(int(item.get('issue_count') or 0) for item in work_loops),
        'action_summary': action_summary,
        'action_history': action_history,
        'remediation_plan': remediation_plan,
        'action_count': action_summary['action_count'],
        'visible_action_count': len(visible_actions),
        'suppressed_action_count': len(suppressed_action_ids),
        'actions': action_summary['actions'],
        'visible_actions': visible_actions,
        'preflights': preflights,
        'evals': evals,
        'remediation_benchmarks': remediation_benchmarks,
        'quality_timeline': quality_timeline,
        'work_loops': work_loops,
    }


def dashboard_markdown(dashboard: dict[str, Any]) -> str:
    lines = [
        '# Work Dashboard',
        '',
        f"- Status: {'pass' if dashboard.get('ok') else 'check'}",
        f"- Preflights: {dashboard.get('preflight_count')} ({dashboard.get('preflight_failures')} failing)",
        f"- Evals: {dashboard.get('eval_count')} ({dashboard.get('eval_failures')} failing)",
        f"- Remediation benchmarks: {dashboard.get('remediation_benchmark_count')} ({dashboard.get('remediation_benchmark_failures')} failing)",
        f"- Quality timeline: {dashboard.get('quality_timeline_event_count', 0)} events, {dashboard.get('quality_timeline_failure_count', 0)} failures, {dashboard.get('quality_timeline_regression_count', 0)} regressions",
        f"- Work loops: {dashboard.get('work_loop_count')} ({dashboard.get('work_loop_failures')} failing)",
        f"- Reviewed failed work loops: {dashboard.get('reviewed_work_loop_failures', 0)}",
        f"- Stale work loops: {dashboard.get('stale_work_loop_count', 0)}",
        f"- Actions: {dashboard.get('action_count', 0)}",
    ]
    remediation_plan = dashboard.get('remediation_plan') if isinstance(dashboard.get('remediation_plan'), dict) else {}
    lines.append(f"- Remediation steps: {remediation_plan.get('step_count', 0)}")
    if remediation_plan.get('markdown_path'):
        lines.append(f"- Remediation plan: [{remediation_plan.get('markdown_path')}]({remediation_plan.get('markdown_path')})")
    drilldowns = dashboard.get('action_drilldowns') if isinstance(dashboard.get('action_drilldowns'), dict) else {}
    if drilldowns.get('index_markdown_path'):
        lines.append(
            f"- Action drilldowns: [{drilldowns.get('index_markdown_path')}]({drilldowns.get('index_markdown_path')})"
        )
    lines.extend(
        [
        '',
        '## Action Summary',
        '',
        '| Severity | Category | Item | Status | Action | Command | Report |',
        '| --- | --- | --- | --- | --- | --- | --- |',
        ]
    )
    actions = dashboard.get('visible_actions') if isinstance(dashboard.get('visible_actions'), list) else []
    if not actions:
        lines.append('| info | dashboard | all | clear | No dashboard actions currently flagged. |  |  |')
    for action in actions[:15]:
        if not isinstance(action, dict):
            continue
        command = action.get('command') or action.get('apply_command') or ''
        command_text = f'`{command}`' if command else ''
        report = action.get('report_path') or ''
        report_text = f'[{report}]({report})' if report else ''
        lines.append(
            '| {severity} | {category} | {item} | {status} | {summary} | {command} | {report} |'.format(
                severity=action.get('severity', ''),
                category=action.get('category', ''),
                item=action.get('subject_id', ''),
                status=action.get('status', ''),
                summary=action.get('summary', ''),
                command=command_text,
                report=report_text,
            )
        )
    history = dashboard.get('action_history') if isinstance(dashboard.get('action_history'), dict) else {}
    if remediation_plan:
        lines.extend(
            [
                '',
                '## Remediation Plan',
                '',
                '| Rank | Severity | Kind | Action | Preview | Apply |',
                '| ---: | --- | --- | --- | --- | --- |',
            ]
        )
        steps = remediation_plan.get('steps') if isinstance(remediation_plan.get('steps'), list) else []
        if not steps:
            lines.append('| 0 | info | none | No high/medium dashboard remediation steps. |  |  |')
        for step in steps[:10]:
            if not isinstance(step, dict):
                continue
            preview = f"`{step.get('preview_command')}`" if step.get('preview_command') else ''
            apply = f"`{step.get('apply_command')}`" if step.get('apply_command') else ''
            lines.append(
                '| {rank} | {severity} | {kind} | {summary} | {preview} | {apply} |'.format(
                    rank=step.get('rank', ''),
                    severity=step.get('severity', ''),
                    kind=step.get('kind', ''),
                    summary=step.get('summary', ''),
                    preview=preview,
                    apply=apply,
                )
            )
    lines.extend(
        [
            '',
            '## Action History',
            '',
        ]
    )
    if history.get('has_previous'):
        previous_path = history.get('previous_snapshot_path') or ''
        previous_link = f' [{previous_path}]({previous_path})' if previous_path else ''
        lines.extend(
            [
                f"- Previous snapshot:{previous_link}".rstrip(),
                f"- New actions: {history.get('new_action_count', 0)}",
                f"- Recurring actions: {history.get('recurring_action_count', 0)}",
                f"- Resolved actions: {history.get('resolved_action_count', 0)}",
            ]
        )
    else:
        lines.append('- Previous snapshot: none yet')
    if history.get('suppressed_action_count'):
        lines.append(f"- Suppressed recurring low-risk actions: {history.get('suppressed_action_count', 0)}")
    lines.extend(
        [
            '',
        '## Recent Work Loops',
        '',
        '| ID | OK | Running | Stale | Reviewed | Issues | PID | Profile | Cycles | Failed | Consecutive | Stop Reason | Cleanup/Review | Report | Events |',
        '| --- | --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- | --- | --- |',
        ]
    )
    for item in dashboard.get('work_loops', []) or []:
        issues = ', '.join(item.get('issue_codes') or []) or 'none'
        cleanup = item.get('cleanup_preview_command') or 'inspect'
        if item.get('cleanup_blockers'):
            cleanup = ', '.join(item.get('cleanup_blockers') or [])
        if item.get('review_preview_command'):
            cleanup = item.get('review_preview_command')
        lines.append(
            '| {id} | {ok} | {running} | {stale} | {reviewed} | {issues} | {pid} | {profile} | {cycles} | {failed} | {consecutive} | {reason} | `{cleanup}` | [{report}]({report}) | [{events}]({events}) |'.format(
                id=item.get('id', ''),
                ok='yes' if item.get('ok') else 'no',
                running='yes' if item.get('in_progress') else 'no',
                stale='yes' if item.get('stale') else 'no',
                reviewed='yes' if item.get('reviewed') else 'no',
                issues=issues,
                pid=item.get('pid') or '',
                profile=item.get('profile', ''),
                cycles=item.get('cycle_count', 0),
                failed=item.get('failed_cycle_count', 0),
                consecutive=item.get('consecutive_failure_count', 0),
                reason=item.get('stop_reason', ''),
                cleanup=cleanup,
                report=item.get('report_path', ''),
                events=item.get('events_path', ''),
            )
        )
    guidance = []
    for item in dashboard.get('work_loops', []) or []:
        if item.get('cleanup_apply_command'):
            if item.get('cleanup_eligible'):
                guidance.append(
                    f"- `{item.get('id')}` stale cleanup: preview with `{item.get('cleanup_preview_command')}`, apply with `{item.get('cleanup_apply_command')}`."
                )
            else:
                guidance.append(
                    f"- `{item.get('id')}` stale legacy cleanup is blocked by {', '.join(item.get('cleanup_blockers') or [])}; apply only after review with `{item.get('cleanup_apply_command')}`."
                )
        elif 'failed' in (item.get('issue_codes') or []):
            guidance.append(
                f"- `{item.get('id')}` failed: inspect [{item.get('report_path')}]({item.get('report_path')}) and [{item.get('events_path')}]({item.get('events_path')}); preview review with `{item.get('review_preview_command')}`, apply with `{item.get('review_apply_command')}`."
            )
        elif 'reviewed_failed' in (item.get('issue_codes') or []):
            guidance.append(
                f"- `{item.get('id')}` reviewed failed loop: acknowledged at {item.get('reviewed_at') or 'unknown'}; note: {item.get('review_note') or 'none'}."
            )
    if guidance:
        lines.extend(['', '### Work Loop Guidance', ''])
        lines.extend(guidance)
    lines.extend(
        [
            '',
            '## Recent Preflights',
            '',
            '| ID | OK | Risks | High | Medium | Dry Run | Probe | Eval | Eval OK | Score Caps | Source Selection | Sources | Blocked | Report | Eval Summary |',
            '| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- |',
        ]
    )
    for item in dashboard.get('preflights', []) or []:
        budget = item.get('latest_budget_totals') if isinstance(item.get('latest_budget_totals'), dict) else {}
        eval_text = 'off'
        if item.get('eval_smoke_enabled'):
            eval_text = (
                f"{item.get('eval_smoke_mode') or 'unknown'} "
                f"{item.get('eval_smoke_task_count', 0)} task(s) / "
                f"{item.get('eval_smoke_average_score', 'n/a')}"
            )
        eval_ok = 'n/a'
        if item.get('eval_smoke_enabled'):
            eval_ok = 'yes' if item.get('eval_smoke_returncode') == 0 else 'no'
        score_caps = f"{item.get('eval_smoke_score_cap_count', 0)} ({_count_summary_text(item.get('eval_smoke_score_caps'))})"
        source_selection = (
            f"{item.get('eval_smoke_buried_strong_selected_count', 0)} buried / "
            f"{item.get('eval_smoke_selected_low_value_source_count', 0)} low / "
            f"{item.get('eval_smoke_planned_low_value_source_count', 0)} planned low"
        )
        eval_summary_path = item.get('eval_smoke_summary_path') or ''
        eval_summary_link = f'[{eval_summary_path}]({eval_summary_path})' if eval_summary_path else 'n/a'
        lines.append(
            '| {id} | {ok} | {risks} | {high} | {medium} | {dry} | {probe} | {eval_text} | {eval_ok} | {score_caps} | {source_selection} | {sources} | {blocked} | [{report}]({report}) | {eval_summary} |'.format(
                id=item.get('id', ''),
                ok='yes' if item.get('ok') else 'no',
                risks=item.get('risk_count', 0),
                high=item.get('high_count', 0),
                medium=item.get('medium_count', 0),
                dry='yes' if item.get('dry_run') else 'no',
                probe='yes' if item.get('probe_tools') else 'no',
                eval_text=eval_text,
                eval_ok=eval_ok,
                score_caps=score_caps,
                source_selection=source_selection,
                sources=budget.get('source_count', 0),
                blocked=budget.get('blocked_source_count', 0),
                report=item.get('report_path', ''),
                eval_summary=eval_summary_link,
            )
        )
    lines.extend(
        [
            '',
            '## Recent Evals',
            '',
            '| ID | OK | Tasks | Average | Trend | Regressions | Improvements | Source Selection Trend | Labels | Threshold Failures | Report |',
            '| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- |',
        ]
    )
    for item in dashboard.get('evals', []) or []:
        labels = ', '.join(f'{key}:{value}' for key, value in sorted((item.get('labels') or {}).items())) or 'none'
        source_trend = 'n/a'
        if item.get('trend_buried_strong_selected_delta') is not None or item.get('trend_selected_low_value_source_delta') is not None:
            source_trend = (
                f"{item.get('trend_buried_strong_selected_delta') or 0:+d} buried / "
                f"{item.get('trend_selected_low_value_source_delta') or 0:+d} low-value"
            )
        lines.append(
            '| {id} | {ok} | {tasks} | {avg} | {trend} | {regressions} | {improvements} | {source_trend} | {labels} | {failures} | [{report}]({report}) |'.format(
                id=item.get('id', ''),
                ok='yes' if item.get('ok') else 'no',
                tasks=item.get('task_count', 0),
                avg=item.get('average_score', 'n/a'),
                trend=item.get('trend_average_score_delta') if item.get('trend_average_score_delta') is not None else 'n/a',
                regressions=item.get('trend_regression_count') if item.get('trend_regression_count') is not None else 'n/a',
                improvements=item.get('trend_improvement_count') if item.get('trend_improvement_count') is not None else 'n/a',
                source_trend=source_trend,
                labels=labels,
                failures=item.get('threshold_failure_count', 0),
                report=item.get('report_path', ''),
            )
        )
    lines.extend(
        [
            '',
            '## Remediation Learning Benchmarks',
            '',
            '| ID | OK | Scenarios | Passed | Failed | Strategy Failures | Trend | Failed Scenarios | Report |',
            '| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |',
        ]
    )
    for item in dashboard.get('remediation_benchmarks', []) or []:
        failed_scenarios = ', '.join(item.get('failed_scenarios') or []) or 'none'
        trend_parts = []
        if item.get('trend_passed_delta') is not None:
            trend_parts.append(f"passed {int(item.get('trend_passed_delta') or 0):+d}")
        if item.get('trend_failed_delta') is not None:
            trend_parts.append(f"failed {int(item.get('trend_failed_delta') or 0):+d}")
        trend = ' / '.join(trend_parts) or 'n/a'
        lines.append(
            '| {id} | {ok} | {scenarios} | {passed} | {failed} | {strategy_failures} | {trend} | {failed_scenarios} | [{report}]({report}) |'.format(
                id=item.get('id', ''),
                ok='yes' if item.get('ok') else 'no',
                scenarios=item.get('scenario_count', 0),
                passed=item.get('passed', 0),
                failed=item.get('failed', 0),
                strategy_failures=item.get('strategy_failure_count', 0),
                trend=trend,
                failed_scenarios=failed_scenarios,
                report=item.get('report_path', ''),
            )
        )
    timeline = dashboard.get('quality_timeline') if isinstance(dashboard.get('quality_timeline'), dict) else {}
    if timeline:
        lines.extend(
            [
                '',
                '## Quality Timeline',
                '',
                '| CI Run | OK | Stack | Eval | Avg | Delta | Remediation | Failed | Risks | Report |',
                '| --- | --- | --- | --- | ---: | ---: | --- | ---: | --- | --- |',
            ]
        )
        for event in timeline.get('events', [])[:10] if isinstance(timeline.get('events'), list) else []:
            if not isinstance(event, dict):
                continue
            risks = ', '.join(event.get('risk_flags') or []) or 'none'
            report = event.get('report_path') or ''
            lines.append(
                '| {id} | {ok} | {stack} | {eval_ok} | {avg} | {delta} | {remediation} | {failed} | {risks} | [{report}]({report}) |'.format(
                    id=event.get('id', ''),
                    ok='yes' if event.get('ok') else 'no',
                    stack='yes' if event.get('stack_ok') else 'no',
                    eval_ok='yes' if event.get('fixture_eval_ok') else 'no',
                    avg=event.get('fixture_eval_average_score') if event.get('fixture_eval_average_score') is not None else 'n/a',
                    delta=event.get('fixture_eval_average_delta') if event.get('fixture_eval_average_delta') is not None else 'n/a',
                    remediation='yes' if event.get('remediation_ok') else 'no',
                    failed=event.get('remediation_failed', 0),
                    risks=risks,
                    report=report,
                )
            )
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='Build a dashboard over saved work preflights and research evals.')
    parser.add_argument('--preflight-root', type=Path, default=DEFAULT_PREFLIGHT_ROOT)
    parser.add_argument('--eval-root', type=Path, default=DEFAULT_EVAL_ROOT)
    parser.add_argument('--ci-check-root', type=Path, default=DEFAULT_CI_CHECK_ROOT)
    parser.add_argument('--work-loop-root', type=Path, default=DEFAULT_WORK_LOOP_ROOT)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--action-history-root', type=Path, default=DEFAULT_ACTION_HISTORY_ROOT)
    parser.add_argument('--action-drilldown-root', type=Path, default=DEFAULT_ACTION_DRILLDOWN_ROOT)
    parser.add_argument('--remediation-plan-output', type=Path, default=DEFAULT_REMEDIATION_PLAN_OUTPUT)
    parser.add_argument('--remediation-events-root', type=Path, default=DEFAULT_REMEDIATION_EVENT_ROOT)
    parser.add_argument('--no-action-history-snapshot', action='store_true')
    parser.add_argument('--no-action-drilldowns', action='store_true')
    parser.add_argument('--no-remediation-plan-file', action='store_true')
    parser.add_argument('--mark-remediation-step', default=None)
    parser.add_argument('--mark-remediation-status', choices=['previewed', 'applied', 'resolved'], default=None)
    parser.add_argument('--mark-remediation-note', default='')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    preflights = collect_preflights(args.preflight_root.expanduser().resolve(), limit=max(1, args.limit))
    evals = collect_evals(args.eval_root.expanduser().resolve(), limit=max(1, args.limit))
    remediation_benchmarks = collect_remediation_benchmarks(args.ci_check_root.expanduser().resolve(), limit=max(1, args.limit))
    quality_timeline = collect_quality_timeline(args.ci_check_root.expanduser().resolve(), limit=max(1, args.limit))
    work_loops = collect_work_loops(args.work_loop_root.expanduser().resolve(), limit=max(1, args.limit))
    action_history_root = args.action_history_root.expanduser().resolve()
    prior_snapshots = load_action_snapshots(action_history_root)
    previous_snapshot = prior_snapshots[0] if prior_snapshots else {}
    previous_actions = previous_snapshot.get('actions') if isinstance(previous_snapshot.get('actions'), list) else []
    remediation_events_root = args.remediation_events_root.expanduser().resolve()
    remediation_execution_events = load_remediation_execution_events(remediation_events_root)
    dashboard = build_dashboard(
        preflights,
        evals,
        work_loops,
        remediation_benchmarks=remediation_benchmarks,
        quality_timeline=quality_timeline,
        previous_actions=previous_actions,
        previous_snapshot_path=previous_snapshot.get('path'),
        prior_action_snapshots=prior_snapshots,
        remediation_execution_events=remediation_execution_events,
    )
    if args.mark_remediation_step or args.mark_remediation_status:
        if not args.mark_remediation_step or not args.mark_remediation_status:
            parser.error('--mark-remediation-step and --mark-remediation-status must be used together')
        event_path = write_remediation_execution_event(
            remediation_events_root,
            step_id=str(args.mark_remediation_step),
            status=str(args.mark_remediation_status),
            note=str(args.mark_remediation_note or ''),
            dashboard=dashboard,
        )
        remediation_execution_events = load_remediation_execution_events(remediation_events_root)
        dashboard['remediation_plan'] = apply_remediation_execution_tracking(
            dashboard.get('remediation_plan') if isinstance(dashboard.get('remediation_plan'), dict) else {},
            remediation_execution_events,
        )
        dashboard['latest_remediation_execution_event_path'] = str(event_path)
    if not args.no_action_drilldowns:
        dashboard['action_drilldowns'] = write_action_drilldown_exports(
            args.action_drilldown_root.expanduser().resolve(),
            dashboard,
        )
    if not args.no_remediation_plan_file:
        plan_paths = write_remediation_plan(
            args.remediation_plan_output.expanduser().resolve(),
            dashboard.get('remediation_plan') if isinstance(dashboard.get('remediation_plan'), dict) else {},
        )
        dashboard['remediation_plan'] = {
            **(dashboard.get('remediation_plan') if isinstance(dashboard.get('remediation_plan'), dict) else {}),
            **plan_paths,
        }
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dashboard_markdown(dashboard), encoding='utf-8')
    json_path = output.with_suffix('.json')
    json_path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding='utf-8')
    if not args.no_action_history_snapshot:
        dashboard['action_history_snapshot_path'] = str(
            write_action_snapshot(action_history_root, dashboard)
        )
        json_path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.json:
        print(json.dumps(dashboard, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({'ok': dashboard['ok'], 'output': str(output), 'json': str(json_path)}, indent=2))
    return 0 if dashboard.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
