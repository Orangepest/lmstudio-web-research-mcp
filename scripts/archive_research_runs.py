from __future__ import annotations

import argparse
import json
import shutil
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_ROOT = REPO_ROOT / '.runtime' / 'research_runs'


@dataclass(frozen=True)
class RunDir:
    path: Path
    run_id: str
    kind: str
    created_at: str
    updated_at: str
    status: str


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def collect_run_dirs(root: Path) -> list[RunDir]:
    if not root.exists():
        return []
    runs: list[RunDir] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith('_'):
            continue
        summary = _read_json(child / 'summary.json')
        run_file = _read_json(child / 'run.json')
        metadata = run_file.get('run') if isinstance(run_file.get('run'), dict) else {}
        run_id = str(summary.get('run_id') or metadata.get('run_id') or child.name)
        kind = str(summary.get('kind') or metadata.get('kind') or 'unknown')
        created_at = str(summary.get('created_at') or metadata.get('created_at') or '')
        updated_at = str(summary.get('updated_at') or metadata.get('updated_at') or created_at)
        status = str(summary.get('status') or metadata.get('status') or 'unknown')
        runs.append(RunDir(path=child, run_id=run_id, kind=kind, created_at=created_at, updated_at=updated_at, status=status))
    runs.sort(key=lambda run: _parse_time(run.updated_at) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return runs


def plan_archive(
    runs: list[RunDir],
    *,
    keep_latest: int,
    older_than_days: int,
    now: datetime | None = None,
) -> list[RunDir]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = now - timedelta(days=max(0, older_than_days))
    keep_latest = max(0, keep_latest)
    candidates: list[RunDir] = []
    for index, run in enumerate(runs):
        updated_at = _parse_time(run.updated_at)
        if index < keep_latest:
            continue
        if updated_at and updated_at > cutoff:
            continue
        candidates.append(run)
    return candidates


def archive_runs(candidates: list[RunDir], *, archive_path: Path) -> Path | None:
    if not candidates:
        return None
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, 'w:gz') as archive:
        for run in candidates:
            archive.add(run.path, arcname=run.path.name)
    for run in candidates:
        shutil.rmtree(run.path)
    return archive_path


def default_archive_path(root: Path) -> Path:
    stamp = datetime.now(UTC).replace(microsecond=0).strftime('%Y%m%dT%H%M%SZ')
    return root / '_archive' / f'research-runs-{stamp}.tar.gz'


def main() -> int:
    parser = argparse.ArgumentParser(description='Preview or archive old persisted research runs.')
    parser.add_argument('--root', default=str(DEFAULT_RUNS_ROOT), help='Research runs directory.')
    parser.add_argument('--keep-latest', type=int, default=50, help='Always keep this many newest runs.')
    parser.add_argument('--older-than-days', type=int, default=30, help='Only archive runs older than this many days.')
    parser.add_argument('--archive-path', default='', help='Archive tar.gz path. Defaults under ROOT/_archive/.')
    parser.add_argument('--apply', action='store_true', help='Create archive and remove archived run directories.')
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    runs = collect_run_dirs(root)
    candidates = plan_archive(runs, keep_latest=args.keep_latest, older_than_days=args.older_than_days)
    archive_path = Path(args.archive_path).expanduser().resolve() if args.archive_path else default_archive_path(root)

    print(f'Research runs root: {root}')
    print(f'Total runs: {len(runs)}')
    print(f'Archive candidates: {len(candidates)}')
    for run in candidates[:20]:
        print(f'- {run.run_id} updated={run.updated_at or "unknown"} status={run.status}')
    if len(candidates) > 20:
        print(f'- ... {len(candidates) - 20} more')

    if not candidates:
        return 0
    if not args.apply:
        print(f'Preview only. Archive would be written to: {archive_path}')
        print('Run again with --apply to archive and remove those run directories.')
        return 0

    written = archive_runs(candidates, archive_path=archive_path)
    print(f'Archived to: {written}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
