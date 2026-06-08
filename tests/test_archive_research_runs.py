from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts.archive_research_runs import archive_runs, collect_run_dirs, plan_archive


def write_run(root: Path, run_id: str, updated_at: str) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / 'summary.json').write_text(
        json.dumps(
            {
                'run_id': run_id,
                'created_at': updated_at,
                'updated_at': updated_at,
                'status': 'completed',
            }
        ),
        encoding='utf-8',
    )
    (run_dir / 'run.json').write_text(json.dumps({'run': {'run_id': run_id}}), encoding='utf-8')
    return run_dir


def write_run_with_metadata(root: Path, run_id: str, updated_at: str, *, kind: str, status: str) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / 'summary.json').write_text(
        json.dumps(
            {
                'run_id': run_id,
                'kind': kind,
                'created_at': updated_at,
                'updated_at': updated_at,
                'status': status,
            }
        ),
        encoding='utf-8',
    )
    (run_dir / 'run.json').write_text(json.dumps({'run': {'run_id': run_id, 'kind': kind, 'status': status}}), encoding='utf-8')
    return run_dir


class ArchiveResearchRunsTests(unittest.TestCase):
    def test_collect_run_dirs_sorts_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run(root, 'old', '2026-01-01T00:00:00Z')
            write_run(root, 'new', '2026-02-01T00:00:00Z')

            runs = collect_run_dirs(root)

        self.assertEqual([run.run_id for run in runs], ['new', 'old'])

    def test_collect_run_dirs_reads_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run_with_metadata(root, 'deep', '2026-01-01T00:00:00Z', kind='deep_research', status='in_progress')

            runs = collect_run_dirs(root)

        self.assertEqual(runs[0].kind, 'deep_research')

    def test_plan_archive_keeps_latest_and_applies_age_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run(root, 'newest', '2026-06-01T00:00:00Z')
            write_run(root, 'old-enough', '2026-04-01T00:00:00Z')
            write_run(root, 'recent-extra', '2026-05-25T00:00:00Z')
            runs = collect_run_dirs(root)

            candidates = plan_archive(
                runs,
                keep_latest=1,
                older_than_days=30,
                now=datetime(2026, 6, 4, tzinfo=UTC),
            )

        self.assertEqual([run.run_id for run in candidates], ['old-enough'])

    def test_archive_runs_writes_tarball_and_removes_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dir = write_run(root, 'old', '2026-01-01T00:00:00Z')
            candidate = collect_run_dirs(root)[0]
            archive_path = root / '_archive' / 'runs.tar.gz'

            written = archive_runs([candidate], archive_path=archive_path)
            with tarfile.open(archive_path, 'r:gz') as archive:
                names = archive.getnames()

            self.assertEqual(written, archive_path)
            self.assertFalse(old_dir.exists())
            self.assertIn('old/run.json', names)

    def test_cli_preview_does_not_remove_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dir = write_run(root, 'old', '2026-01-01T00:00:00Z')

            result = subprocess.run(
                [
                    sys.executable,
                    'scripts/archive_research_runs.py',
                    '--root',
                    str(root),
                    '--keep-latest',
                    '0',
                    '--older-than-days',
                    '1',
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn('Preview only', result.stdout)
            self.assertTrue(old_dir.exists())


if __name__ == '__main__':
    unittest.main()
