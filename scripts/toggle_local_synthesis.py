from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


SYNTHESIS_KEYS = {
    'LOCAL_LLM_REPORT_SYNTHESIS': 'true',
    'LOCAL_LLM_CONTRADICTION_REVIEW': 'true',
}


def set_local_synthesis(data: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    updated = dict(data)
    servers = dict(updated.get('mcpServers') or {})
    web = servers.get('web-research')
    if not isinstance(web, dict):
        raise ValueError('missing web-research MCP server entry')

    web = dict(web)
    env = dict(web.get('env') or {})
    value = 'true' if enabled else 'false'
    for key in SYNTHESIS_KEYS:
        env[key] = value
    env.setdefault('LOCAL_LLM_BASE_URL', 'http://127.0.0.1:1234/v1')
    env.setdefault('LOCAL_LLM_MODEL', 'auto')
    env.setdefault('LOCAL_LLM_TIMEOUT', '8')
    env.setdefault('LOCAL_LLM_REPORT_MAX_TOKENS', '1800')

    web['env'] = env
    servers['web-research'] = web
    updated['mcpServers'] = servers
    return updated


def changed_values(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    before_env = before.get('mcpServers', {}).get('web-research', {}).get('env', {})
    after_env = after.get('mcpServers', {}).get('web-research', {}).get('env', {})
    keys = sorted(set(SYNTHESIS_KEYS) | {
        'LOCAL_LLM_BASE_URL',
        'LOCAL_LLM_MODEL',
        'LOCAL_LLM_TIMEOUT',
        'LOCAL_LLM_REPORT_MAX_TOKENS',
    })
    changes = []
    for key in keys:
        if before_env.get(key) != after_env.get(key):
            changes.append(f'{key}: {before_env.get(key)!r} -> {after_env.get(key)!r}')
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Safely enable or disable optional local-LLM report synthesis in LM Studio mcp.json.'
    )
    state = parser.add_mutually_exclusive_group(required=True)
    state.add_argument('--enable', action='store_true', help='Enable report synthesis and contradiction review.')
    state.add_argument('--disable', action='store_true', help='Disable report synthesis and contradiction review.')
    parser.add_argument('config', nargs='?', default=str(Path.home() / '.lmstudio' / 'mcp.json'))
    parser.add_argument('--apply', action='store_true', help='Write the change. Without this, only preview.')
    parser.add_argument(
        '--backup',
        action='store_true',
        help='When applying, also write a .bak copy beside the config before changing it.',
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    before = json.loads(config_path.read_text(encoding='utf-8'))
    after = set_local_synthesis(before, enabled=args.enable)
    changes = changed_values(before, after)

    if args.apply:
        if args.backup:
            shutil.copy2(config_path, config_path.with_suffix(config_path.suffix + '.bak'))
        config_path.write_text(json.dumps(after, indent=2) + '\n', encoding='utf-8')
        action = 'Updated'
    else:
        action = 'Preview only'

    print(f'{action}: {config_path}')
    if changes:
        print('\n'.join(changes))
    else:
        print('No changes needed')
    if not args.apply:
        print('Run again with --apply to write this config.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
