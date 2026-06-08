#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_EVAL_ROOT = ROOT / '.runtime' / 'evals'


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


def _summary_path(value: Path) -> Path:
    if value.is_dir():
        return value / 'summary.json'
    return value


def load_eval_summary(path: Path) -> dict[str, Any]:
    summary_path = _summary_path(path.expanduser().resolve())
    summary = _load_json(summary_path)
    if not summary:
        return {'ok': False, 'message': f'Could not load eval summary: {summary_path}', 'path': str(summary_path)}
    summary.setdefault('path', str(summary_path))
    summary.setdefault('id', summary_path.parent.name)
    return summary


def list_eval_summaries(root: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    root = root.expanduser().resolve()
    items = []
    for path in root.glob('*/summary.json'):
        summary = load_eval_summary(path)
        if summary.get('ok') is False and summary.get('message'):
            continue
        items.append(summary)
    items.sort(key=lambda item: str(item.get('completed_at') or item.get('id') or ''), reverse=True)
    if limit is not None:
        items = items[: max(0, limit)]
    return items


def _record_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = {}
    for record in summary.get('records', []) or []:
        if not isinstance(record, dict):
            continue
        task = record.get('task') if isinstance(record.get('task'), dict) else {}
        task_id = str(task.get('id') or '')
        if task_id:
            records[task_id] = record
    return records


def _score(record: dict[str, Any] | None) -> int | None:
    if not record:
        return None
    score = record.get('score') if isinstance(record.get('score'), dict) else {}
    if score.get('score') is None:
        return None
    return int(score.get('score') or 0)


def _label(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    score = record.get('score') if isinstance(record.get('score'), dict) else {}
    return str(score.get('label')) if score.get('label') is not None else None


def _metrics(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record:
        return {}
    score = record.get('score') if isinstance(record.get('score'), dict) else {}
    metrics = score.get('metrics') if isinstance(score.get('metrics'), dict) else {}
    return metrics


def _sum_metric(records: dict[str, dict[str, Any]], key: str) -> int:
    return sum(int(_metrics(record).get(key) or 0) for record in records.values())


def _weakest(record: dict[str, Any] | None) -> list[str]:
    if not record:
        return []
    score = record.get('score') if isinstance(record.get('score'), dict) else {}
    return [str(item) for item in score.get('weakest_checks', []) or []]


def _common_weakness_counts(summary: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in summary.get('weakest_checks', []) or []:
        if isinstance(item, dict) and item.get('check'):
            counts[str(item['check'])] = int(item.get('count') or 0)
    if counts:
        return counts
    for record in summary.get('records', []) or []:
        for check in _weakest(record if isinstance(record, dict) else None):
            counts[check] = counts.get(check, 0) + 1
    return counts


def compare_eval_summaries(base: dict[str, Any], compare: dict[str, Any]) -> dict[str, Any]:
    base_records = _record_map(base)
    compare_records = _record_map(compare)
    all_task_ids = sorted(set(base_records) | set(compare_records))
    task_deltas = []
    regressions = []
    improvements = []
    for task_id in all_task_ids:
        base_record = base_records.get(task_id)
        compare_record = compare_records.get(task_id)
        base_score = _score(base_record)
        compare_score = _score(compare_record)
        delta_score = None if base_score is None or compare_score is None else compare_score - base_score
        base_metrics = _metrics(base_record)
        compare_metrics = _metrics(compare_record)
        task_delta = {
            'task_id': task_id,
            'status': 'added' if base_record is None else 'removed' if compare_record is None else 'matched',
            'base_score': base_score,
            'compare_score': compare_score,
            'score_delta': delta_score,
            'base_label': _label(base_record),
            'compare_label': _label(compare_record),
            'source_delta': int(compare_metrics.get('source_count') or 0) - int(base_metrics.get('source_count') or 0),
            'primary_source_delta': int(compare_metrics.get('primary_source_count') or 0) - int(base_metrics.get('primary_source_count') or 0),
            'claim_support_delta': int(compare_metrics.get('indexed_supported_claim_count') or 0)
            - int(base_metrics.get('indexed_supported_claim_count') or 0),
            'unsupported_claim_delta': int(compare_metrics.get('indexed_unsupported_claim_count') or 0)
            - int(base_metrics.get('indexed_unsupported_claim_count') or 0),
            'buried_strong_selected_delta': int(compare_metrics.get('buried_strong_selected_count') or 0)
            - int(base_metrics.get('buried_strong_selected_count') or 0),
            'selected_low_value_source_delta': int(compare_metrics.get('selected_low_value_source_count') or 0)
            - int(base_metrics.get('selected_low_value_source_count') or 0),
            'planned_low_value_source_delta': int(compare_metrics.get('planned_low_value_source_count') or 0)
            - int(base_metrics.get('planned_low_value_source_count') or 0),
            'contradiction_table_row_delta': int(compare_metrics.get('contradiction_table_row_count') or 0)
            - int(base_metrics.get('contradiction_table_row_count') or 0),
            'contradiction_table_resolution_query_delta': int(
                compare_metrics.get('contradiction_table_resolution_query_count') or 0
            )
            - int(base_metrics.get('contradiction_table_resolution_query_count') or 0),
            'intent_quality_delta': round(
                float(compare_metrics.get('average_intent_quality_score') or 0)
                - float(base_metrics.get('average_intent_quality_score') or 0),
                1,
            ),
            'contradiction_resolution_search_delta': int(compare_metrics.get('contradiction_resolution_search_count') or 0)
            - int(base_metrics.get('contradiction_resolution_search_count') or 0),
            'new_weakest_checks': sorted(set(_weakest(compare_record)) - set(_weakest(base_record))),
            'resolved_weakest_checks': sorted(set(_weakest(base_record)) - set(_weakest(compare_record))),
        }
        task_deltas.append(task_delta)
        if delta_score is not None:
            if delta_score <= -5:
                regressions.append(task_delta)
            elif delta_score >= 5:
                improvements.append(task_delta)

    base_weaknesses = _common_weakness_counts(base)
    compare_weaknesses = _common_weakness_counts(compare)
    weakness_deltas = [
        {
            'check': check,
            'base_count': base_weaknesses.get(check, 0),
            'compare_count': compare_weaknesses.get(check, 0),
            'delta': compare_weaknesses.get(check, 0) - base_weaknesses.get(check, 0),
        }
        for check in sorted(set(base_weaknesses) | set(compare_weaknesses))
    ]
    weakness_deltas.sort(key=lambda item: (-abs(int(item['delta'])), item['check']))

    base_average = float(base.get('average_score') or 0)
    compare_average = float(compare.get('average_score') or 0)
    base_buried = _sum_metric(base_records, 'buried_strong_selected_count')
    compare_buried = _sum_metric(compare_records, 'buried_strong_selected_count')
    base_low_value = _sum_metric(base_records, 'selected_low_value_source_count')
    compare_low_value = _sum_metric(compare_records, 'selected_low_value_source_count')
    base_planned_low_value = _sum_metric(base_records, 'planned_low_value_source_count')
    compare_planned_low_value = _sum_metric(compare_records, 'planned_low_value_source_count')
    return {
        'ok': True,
        'base': {
            'id': base.get('id'),
            'path': base.get('path'),
            'completed_at': base.get('completed_at'),
            'task_count': int(base.get('task_count') or len(base_records)),
            'average_score': base_average,
            'labels': base.get('labels') if isinstance(base.get('labels'), dict) else {},
        },
        'compare': {
            'id': compare.get('id'),
            'path': compare.get('path'),
            'completed_at': compare.get('completed_at'),
            'task_count': int(compare.get('task_count') or len(compare_records)),
            'average_score': compare_average,
            'labels': compare.get('labels') if isinstance(compare.get('labels'), dict) else {},
        },
        'delta': {
            'average_score': round(compare_average - base_average, 1),
            'task_count': int(compare.get('task_count') or len(compare_records)) - int(base.get('task_count') or len(base_records)),
            'regression_count': len(regressions),
            'improvement_count': len(improvements),
            'added_task_count': sum(1 for item in task_deltas if item['status'] == 'added'),
            'removed_task_count': sum(1 for item in task_deltas if item['status'] == 'removed'),
            'buried_strong_selected_count': compare_buried,
            'buried_strong_selected_delta': compare_buried - base_buried,
            'selected_low_value_source_count': compare_low_value,
            'selected_low_value_source_delta': compare_low_value - base_low_value,
            'planned_low_value_source_count': compare_planned_low_value,
            'planned_low_value_source_delta': compare_planned_low_value - base_planned_low_value,
        },
        'regressions': sorted(regressions, key=lambda item: int(item.get('score_delta') or 0))[:10],
        'improvements': sorted(improvements, key=lambda item: int(item.get('score_delta') or 0), reverse=True)[:10],
        'task_deltas': task_deltas,
        'weakness_deltas': weakness_deltas[:12],
    }


def compare_eval_runs(base_path: Path, compare_path: Path) -> dict[str, Any]:
    base = load_eval_summary(base_path)
    if base.get('ok') is False and base.get('message'):
        return base
    compare = load_eval_summary(compare_path)
    if compare.get('ok') is False and compare.get('message'):
        return compare
    return compare_eval_summaries(base, compare)


def compare_latest_eval_runs(root: Path, *, offset: int = 0) -> dict[str, Any]:
    summaries = list_eval_summaries(root, limit=max(2 + offset, 2))
    if len(summaries) < 2 + offset:
        return {'ok': False, 'message': 'Need at least two eval summaries to compare.', 'eval_root': str(root)}
    compare = summaries[offset]
    base = summaries[offset + 1]
    return compare_eval_summaries(base, compare)


def comparison_markdown(result: dict[str, Any]) -> str:
    if not result.get('ok'):
        return f"# Eval Trend Comparison\n\n- Error: {result.get('message', 'unknown error')}\n"
    base = result.get('base') if isinstance(result.get('base'), dict) else {}
    compare = result.get('compare') if isinstance(result.get('compare'), dict) else {}
    delta = result.get('delta') if isinstance(result.get('delta'), dict) else {}
    lines = [
        '# Eval Trend Comparison',
        '',
        f"- Base: {base.get('id')} ({base.get('average_score')}/100)",
        f"- Compare: {compare.get('id')} ({compare.get('average_score')}/100)",
        f"- Average score delta: {delta.get('average_score')}",
        f"- Regressions: {delta.get('regression_count')}",
        f"- Improvements: {delta.get('improvement_count')}",
        f"- Added tasks: {delta.get('added_task_count')}",
        f"- Removed tasks: {delta.get('removed_task_count')}",
        '',
        '## Task Score Deltas',
        '',
        '| Task | Base | Compare | Delta | Sources | Primary | Claim Support | Source Selection | Contradiction Table | Intent Quality | Weakness Changes |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |',
    ]
    for item in result.get('task_deltas', []) or []:
        weakness_changes = []
        if item.get('new_weakest_checks'):
            weakness_changes.append('new: ' + ', '.join(item['new_weakest_checks'][:3]))
        if item.get('resolved_weakest_checks'):
            weakness_changes.append('resolved: ' + ', '.join(item['resolved_weakest_checks'][:3]))
        contradiction_table = (
            f"{item.get('contradiction_table_row_delta', 0):+d} rows / "
            f"{item.get('contradiction_table_resolution_query_delta', 0):+d} queries"
        )
        source_selection = (
            f"{item.get('buried_strong_selected_delta', 0):+d} buried / "
            f"{item.get('selected_low_value_source_delta', 0):+d} low-value"
        )
        lines.append(
            '| {task} | {base} | {compare} | {delta} | {sources} | {primary} | {claim} | {source_selection} | {contradiction_table} | {intent} | {weakness} |'.format(
                task=item.get('task_id'),
                base=item.get('base_score') if item.get('base_score') is not None else '',
                compare=item.get('compare_score') if item.get('compare_score') is not None else '',
                delta=item.get('score_delta') if item.get('score_delta') is not None else item.get('status'),
                sources=item.get('source_delta'),
                primary=item.get('primary_source_delta'),
                claim=item.get('claim_support_delta'),
                source_selection=source_selection,
                contradiction_table=contradiction_table,
                intent=item.get('intent_quality_delta'),
                weakness='; '.join(weakness_changes) or '',
            )
        )
    lines.extend(['', '## Common Weakness Deltas', ''])
    if result.get('weakness_deltas'):
        for item in result.get('weakness_deltas', []) or []:
            lines.append(f"- {item.get('check')}: {item.get('base_count')} -> {item.get('compare_count')} ({item.get('delta'):+})")
    else:
        lines.append('- No weakness deltas.')
    return '\n'.join(lines) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description='Compare saved research eval summaries.')
    parser.add_argument('--base', type=Path, default=None, help='Base eval summary.json or eval run directory.')
    parser.add_argument('--compare', type=Path, default=None, help='Compare eval summary.json or eval run directory.')
    parser.add_argument('--eval-root', type=Path, default=DEFAULT_EVAL_ROOT, help='Root containing eval run directories.')
    parser.add_argument('--latest', action='store_true', help='Compare latest eval run against the previous run.')
    parser.add_argument('--offset', type=int, default=0, help='When using --latest, skip this many newest runs first.')
    parser.add_argument('--output-dir', type=Path, default=None)
    args = parser.parse_args()

    if args.base or args.compare:
        if not args.base or not args.compare:
            result = {'ok': False, 'message': 'Provide both --base and --compare, or use --latest.'}
        else:
            result = compare_eval_runs(args.base, args.compare)
    else:
        result = compare_latest_eval_runs(args.eval_root, offset=max(0, args.offset))

    if args.output_dir:
        output_dir = args.output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_json(output_dir / 'eval_comparison.json', result)
        (output_dir / 'eval_comparison.md').write_text(comparison_markdown(result), encoding='utf-8')
    print(json.dumps(result, indent=2, default=_json_default))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
