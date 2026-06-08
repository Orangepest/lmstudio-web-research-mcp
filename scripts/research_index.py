#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_research.index import build_research_index, load_research_index, search_research_index, write_research_index


DEFAULT_INDEX_PATH = ROOT / '.runtime' / 'research_index.json'


def main() -> int:
    parser = argparse.ArgumentParser(description='Build or search the local saved-research sparse vector index.')
    parser.add_argument('--runs-root', type=Path, default=None)
    parser.add_argument('--index-path', type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument('--query', type=str, default=None, help='Search query. If omitted, only builds the index.')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--no-build', action='store_true', help='Search an existing index without rebuilding first.')
    args = parser.parse_args()

    index_path = args.index_path.expanduser().resolve()
    runs_root = args.runs_root.expanduser().resolve() if args.runs_root else None
    if args.no_build:
        index = load_research_index(index_path)
        build_result = {'ok': True, 'path': str(index_path), 'loaded_existing': True, 'entry_count': len(index.get('entries', []) or [])}
    else:
        index = build_research_index(runs_root=runs_root)
        build_result = write_research_index(index, index_path)
    if args.query:
        result = {
            'ok': True,
            'index': build_result,
            'search': search_research_index(index, args.query, limit=args.limit),
        }
    else:
        result = {'ok': True, 'index': build_result}
    print(json.dumps(result, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
