#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_research.remediation_benchmarks import run_remediation_learning_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description='Run deterministic remediation-learning strategy benchmarks.')
    parser.add_argument('--runs-root', type=Path, default=None, help='Optional research-runs root for source-run lookups.')
    parser.add_argument('--json', action='store_true', help='Print JSON instead of a short text summary.')
    args = parser.parse_args()

    result = run_remediation_learning_benchmark(runs_root=args.runs_root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"Remediation learning benchmark: {result['passed']}/{result['scenario_count']} passed"
            f" ({'OK' if result['ok'] else 'CHECK'})"
        )
        for record in result['records']:
            status = 'PASS' if record['ok'] else 'FAIL'
            print(f"- {status} {record['id']}: {record['actual_strategy']} priority={record['actual_priority']}")
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
