#!/usr/bin/env python3
"""
Cleanup script for LM Studio MCP Webcrawler project.

Removes project bloat:
- __pycache__/ directories and *.pyc bytecode files
- .runtime/browser_profile/ (Chromium profiles with caches)
- .runtime/*.log files (old logs)
- .pytest_cache/ (test cache)

Preserves:
- .env (user configuration)
- mcp.json.example (template)
- .gitignore (version control)
- Source code and tests
"""

from __future__ import annotations

import pathlib
import shutil
import sys
from typing import Optional


def get_project_root() -> pathlib.Path:
    """Get the project root directory (parent of scripts/)."""
    return pathlib.Path(__file__).parent.parent


def delete_bloat(root: pathlib.Path, dry_run: bool = False) -> tuple[int, int]:
    """
    Delete bloat items recursively.
    
    Args:
        root: Project root directory
        dry_run: If True, only print what would be deleted without deleting
        
    Returns:
        Tuple of (items_deleted, errors)
    """
    deleted_count = 0
    error_count = 0
    
    # Items to delete
    bloat_patterns = [
        '__pycache__',
        '.pytest_cache',
        '.coverage',
        'htmlcov',
    ]
    
    bloat_files = [
        '.runtime/browser_profile',
        '.runtime/web_research.log',
        '.runtime/chrome_debug.log',
    ]
    
    # Delete directory patterns recursively
    for pattern in bloat_patterns:
        for path in root.rglob(pattern):
            if path.is_dir():
                try:
                    if dry_run:
                        print(f"  [DRY RUN] Would delete directory: {path.relative_to(root)}")
                    else:
                        shutil.rmtree(path, ignore_errors=True)
                        print(f"  ✓ Deleted directory: {path.relative_to(root)}")
                    deleted_count += 1
                except Exception as e:
                    print(f"  ✗ Error deleting {path.relative_to(root)}: {e}")
                    error_count += 1
    
    # Delete specific files and directories
    for item_path in bloat_files:
        path = root / item_path
        if path.exists():
            try:
                if path.is_dir():
                    if dry_run:
                        print(f"  [DRY RUN] Would delete directory: {path.relative_to(root)}")
                    else:
                        shutil.rmtree(path, ignore_errors=True)
                        print(f"  ✓ Deleted directory: {path.relative_to(root)}")
                else:
                    if dry_run:
                        print(f"  [DRY RUN] Would delete file: {path.relative_to(root)}")
                    else:
                        path.unlink()
                        print(f"  ✓ Deleted file: {path.relative_to(root)}")
                deleted_count += 1
            except Exception as e:
                print(f"  ✗ Error deleting {path.relative_to(root)}: {e}")
                error_count += 1
    
    # Delete .pyc and .pyo files recursively
    for pyc_path in root.rglob('*.pyc'):
        try:
            if dry_run:
                print(f"  [DRY RUN] Would delete bytecode: {pyc_path.relative_to(root)}")
            else:
                pyc_path.unlink(missing_ok=True)
            deleted_count += 1
        except Exception as e:
            print(f"  ✗ Error deleting {pyc_path.relative_to(root)}: {e}")
            error_count += 1
    
    for pyo_path in root.rglob('*.pyo'):
        try:
            if dry_run:
                print(f"  [DRY RUN] Would delete bytecode: {pyo_path.relative_to(root)}")
            else:
                pyo_path.unlink(missing_ok=True)
            deleted_count += 1
        except Exception as e:
            print(f"  ✗ Error deleting {pyo_path.relative_to(root)}: {e}")
            error_count += 1
    
    return deleted_count, error_count


def main(dry_run: bool = False) -> int:
    """Main entry point."""
    root = get_project_root()
    
    if not root.exists():
        print(f"✗ Project root not found: {root}")
        return 1
    
    mode = "[DRY RUN] " if dry_run else ""
    print(f"\n{mode}🧹 Cleaning up bloat in {root}...\n")
    
    deleted, errors = delete_bloat(root, dry_run=dry_run)
    
    if dry_run:
        print(f"\n[DRY RUN] Would delete {deleted} items")
    else:
        print(f"\n✅ Cleanup complete! Deleted {deleted} items")
        if errors > 0:
            print(f"⚠️  {errors} errors encountered (see above)")
            return 1
    
    return 0


if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    exit_code = main(dry_run=dry_run)
    sys.exit(exit_code)
