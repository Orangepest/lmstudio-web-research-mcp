from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


EXPECTED_TOOLS = [
    'safe_web_search',
    'safe_repair_tool_call',
    'safe_research_agent',
    'safe_read_url',
    'safe_research',
    'safe_deep_research',
    'safe_research_mission',
    'safe_research_runtime',
    'safe_research_campaign',
    'safe_research_director',
    'safe_synthesize_research_campaign',
    'safe_resume_deep_research',
    'web_search',
    'read_url',
    'discover_links',
    'research_web',
    'deep_research',
    'list_research_runs',
    'safe_work_loop_status',
    'safe_cleanup_work_loops',
    'safe_submit_research_job',
    'safe_research_job_status',
    'safe_cancel_research_job',
    'safe_research_checkpoint_status',
    'safe_interrupt_research_checkpoints',
    'safe_list_research_runs',
    'safe_find_research_runs',
    'safe_research_context',
    'find_research_runs',
    'safe_get_research_run',
    'safe_export_research_run',
    'safe_build_source_pack',
    'get_research_run',
    'invalidate_research_cache',
    'resume_deep_research',
    'continue_research_run',
    'safe_continue_research_run',
]

ADVANCED_TOOLS = [
    'web_search',
    'read_url',
    'discover_links',
    'research_web',
    'deep_research',
    'list_research_runs',
    'find_research_runs',
    'get_research_run',
    'invalidate_research_cache',
    'resume_deep_research',
    'continue_research_run',
]

DEFAULT_EXPECTED_TOOLS = [tool for tool in EXPECTED_TOOLS if tool not in ADVANCED_TOOLS]
CORE_EXPECTED_TOOLS = [
    'safe_repair_tool_call',
    'safe_research_agent',
    'safe_web_search',
    'safe_read_url',
    'safe_research',
    'safe_deep_research',
    'safe_research_runtime',
    'safe_research_director',
]
AGENT_EXPECTED_TOOLS = [
    'safe_repair_tool_call',
    'safe_research_agent',
]
AGENT_STRICT_EXPECTED_TOOLS = [
    'safe_research_agent',
]

def tool_summary(names: list[str], *, target: str, env: dict[str, str] | None = None) -> dict:
    env = env or os.environ
    profile = env.get('MCP_TOOL_PROFILE', 'safe').strip().lower()
    if profile == 'agent_strict':
        expected = AGENT_STRICT_EXPECTED_TOOLS
    elif profile == 'agent':
        expected = AGENT_EXPECTED_TOOLS
    elif profile == 'core':
        expected = CORE_EXPECTED_TOOLS
    else:
        expected = EXPECTED_TOOLS if env.get('MCP_EXPOSE_ADVANCED_TOOLS', '').strip().lower() in {'1', 'true', 'yes', 'on'} else DEFAULT_EXPECTED_TOOLS
    missing = sorted(set(expected) - set(names))
    unexpected = sorted(set(names) - set(expected))
    return {
        'target': target,
        'ok': not missing and not unexpected,
        'tools': names,
        'tool_count': len(names),
        'expected_tools': expected,
        'tool_profile': profile,
        'advanced_tools_exposed': expected == EXPECTED_TOOLS,
        'missing_tools': missing,
        'unexpected_tools': unexpected,
    }


def fetch_status(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read(400).decode('utf-8', errors='replace')
            return {
                'url': url,
                'ok': True,
                'status': response.status,
                'content_type': response.headers.get('Content-Type'),
                'body_preview': body[:200],
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(400).decode('utf-8', errors='replace')
        return {
            'url': url,
            'ok': False,
            'status': exc.code,
            'content_type': exc.headers.get('Content-Type'),
            'body_preview': body[:200],
            'error': str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {'url': url, 'ok': False, 'error': str(exc)}


async def list_tools_http(url: str) -> dict:
    try:
        async with streamablehttp_client(url, timeout=8, sse_read_timeout=8) as (read, write, _session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                return {'url': url, **tool_summary(names, target=url)}
    except Exception as exc:  # noqa: BLE001
        return {'url': url, 'ok': False, 'error': str(exc), 'expected_tools': EXPECTED_TOOLS}


async def list_tools_stdio(command: str, server_args: list[str], *, cwd: str | None = None, env_overrides: dict[str, str] | None = None) -> dict:
    env = dict(os.environ)
    if env_overrides:
        env.update({str(key): str(value) for key, value in env_overrides.items()})
    env['MCP_TRANSPORT'] = 'stdio'
    server = StdioServerParameters(command=command, args=server_args, env=env, cwd=cwd)
    target = ' '.join([command, *server_args])
    try:
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                return tool_summary(names, target=target, env=env)
    except Exception as exc:  # noqa: BLE001
        return {'target': target, 'ok': False, 'error': str(exc), 'expected_tools': EXPECTED_TOOLS}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Probe the lmstudio-web-research MCP server.')
    parser.add_argument('--transport', choices=('stdio', 'http'), default='stdio')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default='8000')
    parser.add_argument('--command', default=sys.executable, help='Server command for stdio mode.')
    parser.add_argument('--server-arg', action='append', dest='server_args', help='Server arg for stdio mode. Repeat as needed.')
    parser.add_argument('--cwd', default=None, help='Working directory for stdio mode.')
    parser.add_argument('--compact', action='store_true', help='Print a compact JSON summary.')
    return parser.parse_args(argv)


def run_http_probe(host: str, port: str) -> dict:
    urls = [
        f'http://{host}:{port}/health',
        f'http://{host}:{port}/mcp',
        f'http://{host}:{port}/sse',
    ]
    mcp_url = f'http://{host}:{port}/mcp'
    results = [fetch_status(url) for url in urls]
    tools = asyncio.run(list_tools_http(mcp_url))
    ok = all(result.get('ok') for result in results) and bool(tools.get('ok'))
    return {'ok': ok, 'transport': 'http', 'results': results, 'tools': tools}


def run_stdio_probe(command: str, server_args: list[str], *, cwd: str | None = None, env_overrides: dict[str, str] | None = None) -> dict:
    tools = asyncio.run(list_tools_stdio(command, server_args, cwd=cwd, env_overrides=env_overrides))
    return {'ok': bool(tools.get('ok')), 'transport': 'stdio', 'tools': tools}


if __name__ == '__main__':
    args = parse_args(sys.argv[1:])
    if args.transport == 'http':
        payload = run_http_probe(args.host, args.port)
    else:
        server_args = args.server_args or ['-m', 'mcp_server.server']
        payload = run_stdio_probe(args.command, server_args, cwd=args.cwd)
    print(json.dumps(payload, separators=(',', ':') if args.compact else None, indent=None if args.compact else 2))
    ok = bool(payload.get('ok'))
    raise SystemExit(0 if ok else 1)
