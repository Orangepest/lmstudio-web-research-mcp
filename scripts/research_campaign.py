#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_research.campaign_synthesis import apply_campaign_narrative_synthesis, build_campaign_synthesis, write_campaign_synthesis_bundle
from web_research.campaigns import create_research_campaign, list_research_campaigns, load_research_campaign
from web_research.config import settings


DEFAULT_CAMPAIGN_ROOT = ROOT / '.runtime' / 'research_campaigns'
DEFAULT_JOBS_ROOT = ROOT / '.runtime' / 'research_jobs'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Plan and manage multi-job research campaigns.')
    parser.add_argument('--root', type=Path, default=DEFAULT_CAMPAIGN_ROOT)
    parser.add_argument('--jobs-root', type=Path, default=DEFAULT_JOBS_ROOT)
    parser.add_argument('--runs-root', type=Path, default=settings.research_runs_dir)
    parser.add_argument('--json', action='store_true')
    subparsers = parser.add_subparsers(dest='command', required=True)

    plan = subparsers.add_parser('plan')
    plan.add_argument('objective')
    plan.add_argument('--profile', default='careful')
    plan.add_argument('--depth', choices=['standard', 'deep', 'exhaustive'], default='standard')
    plan.add_argument('--priority', type=int, default=0)
    plan.add_argument('--queue', action='store_true')

    status = subparsers.add_parser('status')
    status.add_argument('campaign_id', nargs='?')
    status.add_argument('--limit', type=int, default=20)

    synthesize = subparsers.add_parser('synthesize')
    synthesize.add_argument('campaign_id')
    synthesize.add_argument('--output-dir', type=Path, default=ROOT / '.runtime' / 'campaign_syntheses')
    synthesize.add_argument('--apply', action='store_true', help='Write dossier and index files. Defaults to preview only.')
    synthesize.add_argument('--redact', action='store_true')
    synthesize.add_argument('--local-synthesis', action='store_true', help='Ask the configured local LLM to rewrite the final campaign dossier.')

    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    jobs_root = args.jobs_root.expanduser().resolve()
    runs_root = args.runs_root.expanduser().resolve()
    if args.command == 'plan':
        result = create_research_campaign(
            root,
            objective=args.objective,
            profile=args.profile,
            depth=args.depth,
            priority=args.priority,
            queue=args.queue,
            jobs_root=jobs_root,
        )
    elif args.command == 'synthesize':
        synthesis = build_campaign_synthesis(
            args.campaign_id,
            campaign_root=root,
            jobs_root=jobs_root,
            runs_root=runs_root,
            redact=args.redact,
        )
        if synthesis.get('ok') and args.local_synthesis:
            synthesis = asyncio.run(apply_campaign_narrative_synthesis(synthesis, enabled=True))
        if args.apply:
            result = write_campaign_synthesis_bundle(
                args.campaign_id,
                campaign_root=root,
                jobs_root=jobs_root,
                runs_root=runs_root,
                output_dir=args.output_dir.expanduser().resolve(),
                redact=args.redact,
                synthesis=synthesis,
            )
        else:
            result = synthesis
            if result.get('ok'):
                result = {
                    'ok': True,
                    'dry_run': True,
                    'campaign_id': args.campaign_id,
                    'run_count': result.get('run_count'),
                    'source_count': result.get('source_count'),
                    'claim_count': result.get('claim_count'),
                    'missing_runs': result.get('missing_runs'),
                    'campaign_synthesis': result.get('campaign_synthesis'),
                    'message': 'Preview only. Add --apply to write the campaign synthesis bundle.',
                }
    elif args.campaign_id:
        loaded = load_research_campaign(root, args.campaign_id)
        if loaded.get('ok'):
            from web_research.campaigns import summarize_campaign

            result = {
                'ok': True,
                'campaign': summarize_campaign(loaded['campaign'], jobs_root=jobs_root, runs_root=runs_root),
            }
        else:
            result = loaded
    else:
        result = list_research_campaigns(root, limit=args.limit, jobs_root=jobs_root, runs_root=runs_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
