from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


WINDOWS_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
)
POSIX_USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Apple Silicon Mac OS X) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36'
)


def venv_python_path(research_dir: Path, *, platform: str | None = None) -> Path:
    target = (platform or os.name).lower()
    if target in {'nt', 'windows', 'win32'}:
        return research_dir / '.venv' / 'Scripts' / 'python.exe'
    return research_dir / '.venv' / 'bin' / 'python'


def research_entries(research_dir: Path, *, platform: str | None = None) -> dict[str, dict[str, Any]]:
    target = (platform or os.name).lower()
    is_windows = target in {'nt', 'windows', 'win32'}
    return {
        'chrome-mcp-server': {
            'url': 'http://127.0.0.1:12306/mcp',
        },
        'web-research': {
            'command': str(venv_python_path(research_dir, platform=platform)),
            'args': ['-m', 'mcp_server.server'],
            'cwd': str(research_dir),
            'env': {
                'MCP_TRANSPORT': 'stdio',
                'WEB_RESEARCH_LOG_PATH': '.runtime/web_research.log',
                'RESEARCH_RUNS_DIR': '.runtime/research_runs',
                'SEARXNG_URL': 'http://127.0.0.1:8888',
                'SEARCH_PROVIDERS': 'searxng_local_html,searxng_local,brave_html,duckduckgo_lite',
                'SEARCH_TIMEOUT': '4',
                'SEARCH_PROVIDER_BACKOFF_SECONDS': '600',
                'SEARCH_SIMILAR_CACHE': 'true',
                'SEARXNG_ENGINES': 'google',
                'SEARXNG_ENABLED_ENGINES': '',
                'SEARXNG_DISABLED_ENGINES': '',
                'ALLOWED_DOMAINS': '*',
                'USER_AGENT': WINDOWS_USER_AGENT if is_windows else POSIX_USER_AGENT,
                'REQUEST_TIMEOUT': '12',
                'FETCH_BLOCK_BACKOFF_SECONDS': '3',
                'DEEP_RESEARCH_SOFT_TIMEOUT_SECONDS': '35',
                'MAX_CONTENT_CHARS': '40000',
                'CACHE_TTL_SECONDS': '3600',
                'CACHE_MAX_ITEMS': '128',
                'BROWSER_HEADLESS': 'true',
                'BROWSER_TIMEOUT_MS': '12000',
                'BROWSER_MAX_CONTENT_CHARS': '20000',
                'BROWSER_INTERACTION': 'true',
                'BROWSER_SCROLL_STEPS': '4',
                'BROWSER_LOCALE': 'en-US',
                'BROWSER_TIMEZONE_ID': 'UTC' if is_windows else 'Asia/Seoul',
                'BROWSER_PROFILE_DIR': '',
                'MCP_COMPACT_RESULTS': 'true',
                'MCP_TOOL_PROFILE': 'agent_strict',
                'MCP_EXPOSE_ADVANCED_TOOLS': 'false',
                'MCP_RESULT_EXCERPT_CHARS': '3500',
                'MCP_RESULT_MAX_ITEMS': '4',
                'LOCAL_LLM_CONTRADICTION_REVIEW': 'false',
                'LOCAL_LLM_REPORT_SYNTHESIS': 'false',
                'LOCAL_LLM_BASE_URL': 'http://127.0.0.1:1234/v1',
                'LOCAL_LLM_MODEL': 'auto',
                'LOCAL_LLM_TIMEOUT': '8',
                'LOCAL_LLM_REPORT_MAX_TOKENS': '1800',
            },
        },
    }


def merge_config(existing: dict[str, Any], *, research_dir: Path, platform: str | None = None) -> dict[str, Any]:
    data = dict(existing)
    servers = dict(data.get('mcpServers') or {})
    servers.update(research_entries(research_dir, platform=platform))
    data['mcpServers'] = servers
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description='Merge local research MCP servers into an LM Studio MCP config.')
    parser.add_argument('config')
    parser.add_argument('--research-dir', required=True)
    parser.add_argument('--platform', choices=['posix', 'windows'], help='Override path style for generated venv Python command.')
    parser.add_argument('--apply', action='store_true', help='Write the merged config back to the config path.')
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    research_dir = Path(args.research_dir).expanduser()
    existing = json.loads(config_path.read_text()) if config_path.exists() else {}
    merged = merge_config(existing, research_dir=research_dir, platform=args.platform)
    text = json.dumps(merged, indent=2)
    if args.apply:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(f'{text}\n', encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
