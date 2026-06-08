from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CHROME_URL = 'http://127.0.0.1:12306/mcp'
DEFAULT_SEARXNG_URL = 'http://127.0.0.1:8888'


def validate_config(
    data: dict[str, Any],
    *,
    research_dir: Path,
    chrome_url: str = DEFAULT_CHROME_URL,
    check_paths: bool = True,
    platform: str | None = None,
) -> list[str]:
    errors: list[str] = []
    servers = data.get('mcpServers')
    if not isinstance(servers, dict):
        return ['missing or invalid mcpServers object']

    chrome = servers.get('chrome-mcp-server')
    if not isinstance(chrome, dict):
        errors.append('missing chrome-mcp-server entry')
    elif chrome.get('url') != chrome_url:
        errors.append(f'chrome-mcp-server url should be {chrome_url}')

    web = servers.get('web-research')
    if not isinstance(web, dict):
        errors.append('missing web-research entry')
        return errors

    platform_name = (platform or os.name).lower()
    expected_command = (
        research_dir / '.venv' / 'Scripts' / 'python.exe'
        if platform_name in {'nt', 'windows', 'win32'}
        else research_dir / '.venv' / 'bin' / 'python'
    )
    command = Path(str(web.get('command', ''))).expanduser()
    cwd = Path(str(web.get('cwd', ''))).expanduser()
    if command != expected_command:
        errors.append(f'web-research command should be {expected_command}')
    if check_paths and not command.exists():
        errors.append(f'web-research python command missing: {command}')
    if cwd != research_dir:
        errors.append(f'web-research cwd should be {research_dir}')
    if web.get('args') != ['-m', 'mcp_server.server']:
        errors.append("web-research args should be ['-m', 'mcp_server.server']")

    env = web.get('env')
    if not isinstance(env, dict):
        errors.append('web-research env should be an object')
        return errors
    is_windows = platform_name in {'nt', 'windows', 'win32'}
    expected_env = {
        'MCP_TRANSPORT': 'stdio',
        'WEB_RESEARCH_LOG_PATH': '.runtime/web_research.log',
        'RESEARCH_RUNS_DIR': '.runtime/research_runs',
        'SEARXNG_URL': DEFAULT_SEARXNG_URL,
        'SEARCH_PROVIDERS': 'searxng_local_html,searxng_local,brave_html,duckduckgo_lite',
        'SEARCH_TIMEOUT': '4',
        'SEARCH_PROVIDER_BACKOFF_SECONDS': '600',
        'SEARCH_SIMILAR_CACHE': 'true',
        'SEARXNG_ENGINES': 'google',
        'SEARXNG_ENABLED_ENGINES': '',
        'SEARXNG_DISABLED_ENGINES': '',
        'ALLOWED_DOMAINS': '*',
        'REQUEST_TIMEOUT': '12',
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
    }
    for key, expected in expected_env.items():
        if env.get(key) != expected:
            errors.append(f'web-research env {key} should be {expected!r}')
    if not env.get('USER_AGENT'):
        errors.append('web-research env USER_AGENT is required')

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate LM Studio MCP config for the local research stack.')
    parser.add_argument('config', nargs='?', default=str(Path.home() / '.lmstudio' / 'mcp.json'))
    parser.add_argument(
        '--research-dir',
        default=str(Path.home() / 'mcp-servers' / 'lmstudio-web-research-mcp'),
    )
    parser.add_argument('--platform', choices=['posix', 'windows'], help='Override expected venv Python path style.')
    parser.add_argument('--check-paths', action='store_true', default=True, help='Require configured command paths to exist.')
    parser.add_argument('--no-check-paths', action='store_false', dest='check_paths', help='Validate shape only, without checking local files.')
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    research_dir = Path(args.research_dir).expanduser()
    data = json.loads(config_path.read_text())
    errors = validate_config(data, research_dir=research_dir, platform=args.platform, check_paths=args.check_paths)
    if errors:
        print('\n'.join(errors))
        return 1
    print('LM Studio MCP config shape is valid')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
