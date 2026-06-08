from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.archive_research_runs import collect_run_dirs, plan_archive
from scripts.probe_mcp_server import EXPECTED_TOOLS, run_stdio_probe
from scripts.show_research_preset import DEFAULT_DOC_PATH, DEFAULT_OUTPUT_PATH, read_system_prompt, write_prompt_file
from scripts.validate_lmstudio_mcp import validate_config
from mcp_server.debug_tools import list_declared_tool_names
from web_research.report import build_source_policy_audit
from web_research.runs import run_budget_summary
from web_research.search import web_search


DEFAULT_CONFIG_PATH = Path.home() / '.lmstudio' / 'mcp.json'
DEFAULT_README_PATH = REPO_ROOT / 'README.md'
DEFAULT_SEARCH_PROVIDERS = 'searxng_local_html,searxng_local,brave_html,duckduckgo_lite'
KNOWN_SEARCH_PROVIDERS = {
    'searxng_local_html',
    'searxng_local',
    'mojeek_html',
    'brave_html',
    'duckduckgo_html',
    'duckduckgo_lite',
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_run_budget(run_id: str, *, root: Path) -> dict[str, Any]:
    run_path = root / run_id / 'run.json'
    try:
        run = json.loads(run_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    payload = run.get('payload') if isinstance(run.get('payload'), dict) else {}
    budget = payload.get('budget') if isinstance(payload.get('budget'), dict) else {}
    return budget or run_budget_summary(payload)


def _load_run_payload(run_id: str, *, root: Path) -> dict[str, Any]:
    run_path = root / run_id / 'run.json'
    try:
        run = json.loads(run_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    payload = run.get('payload') if isinstance(run.get('payload'), dict) else {}
    return payload if isinstance(payload, dict) else {}


def _aggregate_source_policy_audits(audits: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        'skipped_source_count': 0,
        'trace_skipped_source_count': 0,
        'recovery_skip_count': 0,
        'trace_recovery_skip_count': 0,
        'hard_block_recovery_skip_count': 0,
    }
    skip_reason_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    samples = []
    warnings = set()
    for audit in audits:
        for key in totals:
            totals[key] += int(audit.get(key) or 0)
        for reason, count in (audit.get('skip_reason_counts') or {}).items():
            skip_reason_counts[str(reason)] = skip_reason_counts.get(str(reason), 0) + int(count or 0)
        for item in audit.get('skipped_domains', []) or []:
            if not isinstance(item, dict):
                continue
            domain = str(item.get('domain') or '').strip()
            if domain:
                domain_counts[domain] = domain_counts.get(domain, 0) + int(item.get('count') or 0)
        for warning in audit.get('warnings', []) or []:
            warnings.add(str(warning))
        for sample in audit.get('samples', []) or []:
            if isinstance(sample, dict) and len(samples) < 8:
                samples.append(sample)
    return {
        'ok': not bool(warnings),
        **totals,
        'skip_reason_counts': skip_reason_counts,
        'skipped_domains': [
            {'domain': domain, 'count': count}
            for domain, count in sorted(domain_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        'samples': samples,
        'warnings': sorted(warnings),
    }


def prompt_status(*, doc_path: Path = DEFAULT_DOC_PATH, output_path: Path = DEFAULT_OUTPUT_PATH) -> dict[str, Any]:
    status: dict[str, Any] = {
        'doc_path': str(doc_path),
        'output_path': str(output_path),
        'doc_exists': doc_path.exists(),
        'output_exists': output_path.exists(),
    }
    try:
        prompt = read_system_prompt(doc_path)
    except Exception as exc:  # noqa: BLE001
        status.update({'ok': False, 'error': str(exc)})
        return status
    output_text = output_path.read_text(encoding='utf-8') if output_path.exists() else ''
    output_matches_doc = output_text.strip() == prompt.strip()
    status.update(
        {
            'ok': output_matches_doc,
            'prompt_chars': len(prompt),
            'output_matches_doc': output_matches_doc,
            'mentions_safe_tools': 'safe_research' in prompt and 'safe_deep_research' in prompt,
        }
    )
    return status


def refresh_prompt_file(*, doc_path: Path = DEFAULT_DOC_PATH, output_path: Path = DEFAULT_OUTPUT_PATH) -> dict[str, Any]:
    try:
        prompt = read_system_prompt(doc_path)
        written = write_prompt_file(prompt, output_path)
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': str(exc), 'output_path': str(output_path)}
    return {
        'ok': True,
        'message': 'Prompt file refreshed from docs.',
        'output_path': str(written),
        'prompt_chars': len(prompt),
    }


def config_status(*, config_path: Path, research_dir: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {'ok': False, 'path': str(config_path), 'errors': ['config file missing']}
    data = _load_json(config_path)
    errors = validate_config(data, research_dir=research_dir)
    web = data.get('mcpServers', {}).get('web-research', {}) if isinstance(data.get('mcpServers'), dict) else {}
    env = web.get('env', {}) if isinstance(web, dict) else {}
    return {
        'ok': not errors,
        'path': str(config_path),
        'errors': errors,
        'compact_results': env.get('MCP_COMPACT_RESULTS'),
        'result_excerpt_chars': env.get('MCP_RESULT_EXCERPT_CHARS'),
        'result_max_items': env.get('MCP_RESULT_MAX_ITEMS'),
        'tool_profile': env.get('MCP_TOOL_PROFILE'),
        'advanced_tools': env.get('MCP_EXPOSE_ADVANCED_TOOLS'),
        'max_content_chars': env.get('MAX_CONTENT_CHARS'),
        'browser_max_content_chars': env.get('BROWSER_MAX_CONTENT_CHARS'),
        'browser_interaction': env.get('BROWSER_INTERACTION'),
        'browser_scroll_steps': env.get('BROWSER_SCROLL_STEPS'),
        'search_providers': env.get('SEARCH_PROVIDERS'),
        'search_timeout': env.get('SEARCH_TIMEOUT'),
        'searxng_engines': env.get('SEARXNG_ENGINES'),
        'local_synthesis': env.get('LOCAL_LLM_REPORT_SYNTHESIS'),
        'contradiction_review': env.get('LOCAL_LLM_CONTRADICTION_REVIEW'),
    }


def _configured_search_env(config_path: Path) -> dict[str, str]:
    data = _load_json(config_path)
    servers = data.get('mcpServers') if isinstance(data.get('mcpServers'), dict) else {}
    web = servers.get('web-research') if isinstance(servers, dict) else {}
    env = web.get('env') if isinstance(web, dict) else {}
    return {str(key): str(value) for key, value in env.items()} if isinstance(env, dict) else {}


def _search_provider_order(env: dict[str, str]) -> list[str]:
    raw = env.get('SEARCH_PROVIDERS') or DEFAULT_SEARCH_PROVIDERS
    providers = [item.strip().lower() for item in raw.split(',') if item.strip()]
    configured = [provider for provider in providers if provider in KNOWN_SEARCH_PROVIDERS]
    return configured or DEFAULT_SEARCH_PROVIDERS.split(',')


def _latest_lmstudio_log_path() -> Path | None:
    root = Path.home() / '.lmstudio' / 'server-logs'
    if not root.exists():
        return None
    candidates = [path for path in root.glob('*/*.log') if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def _provider_from_search_url(url: str, *, searxng_url: str | None = None) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()
    searxng_host = urlparse(searxng_url or '').netloc.lower()
    if (searxng_host and host == searxng_host) or host in {'127.0.0.1:8888', 'localhost:8888'}:
        return 'searxng_local' if 'format=json' in query else 'searxng_local_html'
    if 'mojeek.com' in host:
        return 'mojeek_html'
    if 'search.brave.com' in host:
        return 'brave_html'
    if 'lite.duckduckgo.com' in host:
        return 'duckduckgo_lite'
    if 'duckduckgo.com' in host and '/html' in path:
        return 'duckduckgo_html'
    return None


def _recent_search_failures(log_path: Path | None, *, searxng_url: str | None, max_lines: int = 500) -> dict[str, Any]:
    if not log_path or not log_path.exists():
        return {'log_path': str(log_path) if log_path else None, 'by_provider': {}, 'count': 0, 'samples': []}
    try:
        lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-max_lines:]
    except OSError:
        return {'log_path': str(log_path), 'by_provider': {}, 'count': 0, 'samples': []}
    by_provider: dict[str, int] = {}
    samples = []
    pattern = re.compile(r'HTTP Request: GET (?P<url>\S+) "HTTP/[^"]+" (?P<status>\d{3})')
    for line in lines:
        match = pattern.search(line)
        if not match:
            continue
        status = match.group('status')
        if status not in {'403', '429', '500', '502', '503', '504'}:
            continue
        provider = _provider_from_search_url(match.group('url'), searxng_url=searxng_url)
        if not provider:
            continue
        by_provider[provider] = by_provider.get(provider, 0) + 1
        if len(samples) < 5:
            samples.append({'provider': provider, 'status': status, 'url': match.group('url')[:240]})
    return {'log_path': str(log_path), 'by_provider': by_provider, 'count': sum(by_provider.values()), 'samples': samples}


def lmstudio_runtime_status(log_path: Path | None = None, *, max_lines: int = 1000) -> dict[str, Any]:
    log_path = log_path or _latest_lmstudio_log_path()
    if not log_path or not log_path.exists():
        return {'ok': True, 'log_path': str(log_path) if log_path else None, 'warnings': []}
    try:
        lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-max_lines:]
    except OSError as exc:
        return {'ok': False, 'log_path': str(log_path), 'warnings': [f'Could not read LM Studio log: {exc}']}

    truncation_count = 0
    max_prompt_tokens = 0
    websocket_closes = 0
    plugin_starts = []
    tool_call_requests = 0
    last_log_at = None
    last_truncation_at = None
    last_websocket_close_at = None
    last_tool_call_at = None
    parse_error_samples = []
    timestamp_pattern = re.compile(r'^\[(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')
    truncation_pattern = re.compile(r"TruncateMiddle policy activated.*?'(?P<tokens>\d+)' token prompt")
    plugin_pattern = re.compile(r'Starting MCP server .*tools=(?P<tools>\d+) tool_profile=(?P<profile>\S+) advanced_tools=(?P<advanced>\S+)')
    for line in lines:
        timestamp = timestamp_pattern.search(line)
        if timestamp:
            last_log_at = timestamp.group('timestamp')
        truncation = truncation_pattern.search(line)
        if truncation:
            truncation_count += 1
            max_prompt_tokens = max(max_prompt_tokens, int(truncation.group('tokens')))
            last_truncation_at = last_log_at
        if 'WebSocket connection closed' in line:
            websocket_closes += 1
            last_websocket_close_at = last_log_at
        if 'Processing request of type CallToolRequest' in line:
            tool_call_requests += 1
            last_tool_call_at = last_log_at
        plugin = plugin_pattern.search(line)
        if plugin:
            plugin_starts.append(
                {
                    'tools': int(plugin.group('tools')),
                    'tool_profile': plugin.group('profile'),
                    'advanced_tools': plugin.group('advanced'),
                }
            )
        lowered = line.lower()
        if ('failed to parse tool call' in lowered or 'expected "<parameter=' in lowered) and len(parse_error_samples) < 3:
            parse_error_samples.append(line[-300:])

    warnings = []
    if truncation_count:
        warnings.append('LM Studio is truncating the chat context; start a fresh chat or lower retained context for fewer malformed tool calls.')
    if websocket_closes:
        warnings.append('LM Studio MCP bridge WebSocket closed recently; reload the MCP plugin if tools stop responding.')
    if parse_error_samples:
        warnings.append('LM Studio logged tool-call parse errors; use the strict one-tool prompt and fresh context.')
    latest_plugin_start = plugin_starts[-1] if plugin_starts else None
    return {
        'ok': not bool(websocket_closes or parse_error_samples),
        'log_path': str(log_path),
        'last_log_at': last_log_at,
        'truncation_count': truncation_count,
        'max_prompt_tokens': max_prompt_tokens,
        'last_truncation_at': last_truncation_at,
        'websocket_closes': websocket_closes,
        'last_websocket_close_at': last_websocket_close_at,
        'tool_call_requests': tool_call_requests,
        'last_tool_call_at': last_tool_call_at,
        'latest_plugin_start': latest_plugin_start,
        'parse_error_samples': parse_error_samples,
        'warnings': warnings,
    }


def search_provider_status(
    *,
    config_path: Path,
    probe: bool = False,
    probe_query: str = 'search provider health check current news',
) -> dict[str, Any]:
    env = _configured_search_env(config_path)
    provider_order = _search_provider_order(env)
    searxng_url = env.get('SEARXNG_URL') or 'http://127.0.0.1:8888'
    recent_failures = _recent_search_failures(_latest_lmstudio_log_path(), searxng_url=searxng_url)
    configured = {
        'provider_order': provider_order,
        'search_timeout': env.get('SEARCH_TIMEOUT') or '4',
        'provider_backoff_seconds': env.get('SEARCH_PROVIDER_BACKOFF_SECONDS') or '600',
        'similar_cache': env.get('SEARCH_SIMILAR_CACHE') or 'true',
        'searxng_url': searxng_url,
        'searxng_engines': env.get('SEARXNG_ENGINES') or 'google',
        'searxng_enabled_engines': env.get('SEARXNG_ENABLED_ENGINES') or '',
        'searxng_disabled_engines': env.get('SEARXNG_DISABLED_ENGINES') or '',
    }
    live_probe: dict[str, Any] | None = None
    if probe:
        result = web_search(probe_query, max_results=3)
        live_probe = {
            'ok': bool(result.get('ok')),
            'query': probe_query,
            'provider': result.get('provider'),
            'provider_order': result.get('provider_order'),
            'backend_attempts': result.get('backend_attempts', []),
            'result_count': len(result.get('results', []) or []),
            'message': result.get('message'),
        }
    warnings = []
    if recent_failures.get('count'):
        warnings.append('Recent LM Studio logs contain search-provider HTTP failures.')
    if provider_order and provider_order[0] == 'searxng_local' and 'searxng_local_html' in provider_order:
        warnings.append('JSON SearXNG is before HTML fallback; HTML-first is usually faster and less brittle.')
    if live_probe and not live_probe.get('ok'):
        warnings.append('Live search-provider probe failed.')
    return {
        'ok': not bool(live_probe and not live_probe.get('ok')),
        'configured': configured,
        'recent_failures': recent_failures,
        'live_probe': live_probe,
        'warnings': warnings,
    }


def docs_status(*, readme_path: Path = DEFAULT_README_PATH, doc_path: Path = DEFAULT_DOC_PATH) -> dict[str, Any]:
    server_tools = list_declared_tool_names()
    safe_tools = [name for name in server_tools if name.startswith('safe_')]
    try:
        readme = readme_path.read_text(encoding='utf-8')
        section = readme.split('## MCP Tools', 1)[1].split('## Quick Start', 1)[0]
        documented_tools = re.findall(r'^- `([a-zA-Z0-9_]+)\(', section, flags=re.MULTILINE)
    except Exception as exc:  # noqa: BLE001
        return {
            'ok': False,
            'readme_path': str(readme_path),
            'doc_path': str(doc_path),
            'message': f'Could not parse README MCP Tools section: {exc}',
        }
    try:
        prompt = read_system_prompt(doc_path)
    except Exception as exc:  # noqa: BLE001
        return {
            'ok': False,
            'readme_path': str(readme_path),
            'doc_path': str(doc_path),
            'message': f'Could not parse LM Studio prompt: {exc}',
        }
    readme_missing = [name for name in server_tools if name not in documented_tools]
    readme_unexpected = [name for name in documented_tools if name not in server_tools]
    prompt_missing_safe_tools = [name for name in safe_tools if name not in prompt]
    readme_order_matches = documented_tools == server_tools
    ok = not readme_missing and not readme_unexpected and not prompt_missing_safe_tools and readme_order_matches
    return {
        'ok': ok,
        'readme_path': str(readme_path),
        'doc_path': str(doc_path),
        'readme_tool_count': len(documented_tools),
        'expected_tool_count': len(server_tools),
        'server_tools': server_tools,
        'readme_order_matches': readme_order_matches,
        'readme_missing_tools': readme_missing,
        'readme_unexpected_tools': readme_unexpected,
        'prompt_missing_safe_tools': prompt_missing_safe_tools,
    }


def runs_status(*, root: Path, keep_latest: int, older_than_days: int) -> dict[str, Any]:
    runs = collect_run_dirs(root)
    archive_candidates = plan_archive(runs, keep_latest=keep_latest, older_than_days=older_than_days)
    statuses: dict[str, int] = {}
    for run in runs:
        statuses[run.status] = statuses.get(run.status, 0) + 1

    def suggested_actions(run: Any) -> list[dict[str, str]]:
        actions = []
        if run.kind == 'deep_research' and run.status == 'in_progress':
            actions.append(
                {
                    'tool': 'safe_resume_deep_research',
                    'reason': 'Run is an interrupted deep_research checkpoint.',
                    'example': f'safe_resume_deep_research(run_id="{run.run_id}")',
                }
            )
        if run.status == 'completed':
            actions.append(
                {
                    'tool': 'safe_continue_research_run',
                    'reason': 'Run is completed and can be extended with a follow-up query.',
                    'example': f'safe_continue_research_run(request="{run.run_id}\\n<follow-up query>")',
                }
            )
            actions.append(
                {
                    'tool': 'safe_export_research_run',
                    'reason': 'Run is completed and can be exported as a review/share bundle.',
                    'example': f'safe_export_research_run(request="{run.run_id}\\nprofile=private-share")',
                }
            )
            actions.append(
                {
                    'tool': 'safe_build_source_pack',
                    'reason': 'Run is completed and can be included in a redacted source handoff pack.',
                    'example': f'safe_build_source_pack(request="{run.run_id}")',
                }
            )
        return actions

    latest = []
    aggregate_budget: dict[str, int] = {}
    source_policy_audits: list[dict[str, Any]] = []
    for run in runs[:5]:
        budget = _load_run_budget(run.run_id, root=root)
        payload = _load_run_payload(run.run_id, root=root)
        source_policy_audit = (
            payload.get('source_policy_audit')
            if isinstance(payload.get('source_policy_audit'), dict)
            else build_source_policy_audit(payload)
        )
        source_policy_audits.append(source_policy_audit)
        for key, value in budget.items():
            if isinstance(value, int):
                aggregate_budget[key] = aggregate_budget.get(key, 0) + value
        latest.append(
            {
                'run_id': run.run_id,
                'kind': run.kind,
                'updated_at': run.updated_at,
                'status': run.status,
                'budget': budget,
                'source_policy_audit': source_policy_audit,
                'suggested_actions': suggested_actions(run),
            }
        )
    resumable = [
        {
            'run_id': run.run_id,
            'kind': run.kind,
            'updated_at': run.updated_at,
            'resume_tool_call': f'safe_resume_deep_research(run_id="{run.run_id}")',
        }
        for run in runs
        if run.kind == 'deep_research' and run.status == 'in_progress'
    ][:5]
    return {
        'ok': True,
        'root': str(root),
        'total_runs': len(runs),
        'status_counts': statuses,
        'archive_candidates': len(archive_candidates),
        'latest_budget_totals': aggregate_budget,
        'latest_source_policy_audit': _aggregate_source_policy_audits(source_policy_audits),
        'latest': latest,
        'resumable': resumable,
    }


def tools_status(*, research_dir: Path, probe: bool, config_path: Path | None = None) -> dict[str, Any]:
    command = research_dir / '.venv' / 'bin' / 'python'
    env = _configured_search_env(config_path) if config_path else {}
    if not probe:
        profile = env.get('MCP_TOOL_PROFILE', 'safe')
        return {
            'ok': None,
            'probe_skipped': True,
            'tool_profile': profile,
        }
    return run_stdio_probe(str(command), ['-m', 'mcp_server.server'], cwd=str(research_dir), env_overrides=env).get('tools', {})


def build_status(
    *,
    config_path: Path,
    research_dir: Path,
    runs_root: Path,
    probe_tools: bool,
    probe_search: bool = False,
    refresh_prompt: bool = False,
    keep_latest: int = 50,
    older_than_days: int = 30,
) -> dict[str, Any]:
    refresh = refresh_prompt_file() if refresh_prompt else None
    prompt = prompt_status()
    docs = docs_status()
    config = config_status(config_path=config_path, research_dir=research_dir)
    search = search_provider_status(config_path=config_path, probe=probe_search)
    lmstudio = lmstudio_runtime_status()
    runs = runs_status(root=runs_root, keep_latest=keep_latest, older_than_days=older_than_days)
    tools = tools_status(research_dir=research_dir, probe=probe_tools, config_path=config_path)
    ok_parts = [
        bool(prompt.get('ok')),
        bool(docs.get('ok')),
        bool(config.get('ok')),
        bool(search.get('ok')),
        bool(lmstudio.get('ok')),
        bool(runs.get('ok')),
    ]
    if refresh_prompt:
        ok_parts.append(bool(refresh and refresh.get('ok')))
    if probe_tools:
        ok_parts.append(bool(tools.get('ok')))
    return {
        'ok': all(ok_parts),
        'refresh_prompt': refresh,
        'prompt': prompt,
        'docs': docs,
        'config': config,
        'search': search,
        'lmstudio_runtime': lmstudio,
        'runs': runs,
        'tools': tools,
    }


def format_status(status: dict[str, Any]) -> str:
    prompt = status['prompt']
    config = status['config']
    search = status.get('search') or {}
    lmstudio = status.get('lmstudio_runtime') or {}
    docs = status.get('docs') or {}
    runs = status['runs']
    tools = status['tools']
    refresh = status.get('refresh_prompt')
    lines = [
        f'Research stack status: {"OK" if status.get("ok") else "CHECK"}',
    ]
    if refresh:
        lines.extend(
            [
                '',
                f'Prompt refresh: {"OK" if refresh.get("ok") else "CHECK"}',
                f'- {refresh.get("message")}',
                f'- output: {refresh.get("output_path")}',
            ]
        )
    if status.get('dry_run'):
        dry_run = status.get('dry_run') if isinstance(status.get('dry_run'), dict) else {}
        lines.extend(['', 'Dry run: enabled', f'- {dry_run.get("message", "No network or MCP probe was run.")}'])
    lines.extend(
        [
            '',
            f'Prompt: {"OK" if prompt.get("ok") else "CHECK"}',
            f'- guide: {prompt.get("doc_path")}',
            f'- prompt file: {prompt.get("output_path")}',
            f'- output matches guide: {prompt.get("output_matches_doc")}',
            '',
            f'Docs alignment: {"OK" if docs.get("ok") else "CHECK"}',
            f'- README tool order matches server: {docs.get("readme_order_matches")}',
            f'- prompt missing safe tools: {docs.get("prompt_missing_safe_tools")}',
            '',
        ]
    )
    lines.extend(
        [
        f'LM Studio config: {"OK" if config.get("ok") else "CHECK"}',
        f'- path: {config.get("path")}',
        f'- compact results: {config.get("compact_results")}',
        f'- result excerpt chars: {config.get("result_excerpt_chars")}',
        f'- result max items: {config.get("result_max_items")}',
        f'- tool profile: {config.get("tool_profile")}',
        f'- advanced tools: {config.get("advanced_tools")}',
        f'- max content chars: {config.get("max_content_chars")}',
        f'- browser max content chars: {config.get("browser_max_content_chars")}',
        f'- browser interaction: {config.get("browser_interaction")}',
        f'- browser scroll steps: {config.get("browser_scroll_steps")}',
        f'- search providers: {config.get("search_providers")}',
        f'- search timeout: {config.get("search_timeout")}',
        f'- searxng engines: {config.get("searxng_engines")}',
        f'- local synthesis: {config.get("local_synthesis")}',
        f'- contradiction review: {config.get("contradiction_review")}',
        '',
        f'Search providers: {"OK" if search.get("ok") else "CHECK"}',
        f'- order: {(search.get("configured") or {}).get("provider_order")}',
        f'- timeout: {(search.get("configured") or {}).get("search_timeout")}',
        f'- searxng: {(search.get("configured") or {}).get("searxng_url")} engines={(search.get("configured") or {}).get("searxng_engines")}',
        f'- recent failures: {(search.get("recent_failures") or {}).get("by_provider")}',
        f'- warnings: {search.get("warnings")}',
        '',
        f'LM Studio runtime: {"OK" if lmstudio.get("ok") else "CHECK"}',
        f'- log: {lmstudio.get("log_path")}',
        f'- last log line: {lmstudio.get("last_log_at")}',
        f'- context truncations: {lmstudio.get("truncation_count")} max_prompt_tokens={lmstudio.get("max_prompt_tokens")}',
        f'- last truncation: {lmstudio.get("last_truncation_at")}',
        f'- websocket closes: {lmstudio.get("websocket_closes")}',
        f'- last websocket close: {lmstudio.get("last_websocket_close_at")}',
        f'- last tool call: {lmstudio.get("last_tool_call_at")}',
        f'- latest plugin start: {lmstudio.get("latest_plugin_start")}',
        f'- warnings: {lmstudio.get("warnings")}',
        '',
        'Research runs:',
        f'- total: {runs.get("total_runs")}',
        f'- archive candidates with current defaults: {runs.get("archive_candidates")}',
        f'- statuses: {runs.get("status_counts")}',
        f'- latest budget totals: {runs.get("latest_budget_totals")}',
        f'- latest source policy audit: {runs.get("latest_source_policy_audit")}',
        ]
    )
    resumable = runs.get('resumable') or []
    if resumable:
        lines.append('- resumable in-progress runs:')
        for item in resumable:
            lines.append(f'  - {item.get("run_id")} updated={item.get("updated_at")}')
            lines.append(f'    {item.get("resume_tool_call")}')
    latest = runs.get('latest') or []
    if latest:
        lines.append('- latest run next actions:')
        for item in latest[:3]:
            actions = item.get('suggested_actions') or []
            if not actions:
                continue
            tools_text = ', '.join(str(action.get('tool')) for action in actions if isinstance(action, dict) and action.get('tool'))
            lines.append(f'  - {item.get("run_id")} status={item.get("status")}: {tools_text}')
    lines.extend(['', 'MCP tools:'])
    if tools.get('probe_skipped'):
        lines.append(f'- probe skipped; expected tools: {tools.get("expected_tool_count")}')
    else:
        lines.extend(
            [
                f'- ok: {tools.get("ok")}',
                f'- count: {tools.get("tool_count")}',
                f'- missing: {tools.get("missing_tools")}',
                f'- unexpected: {tools.get("unexpected_tools")}',
            ]
        )
    errors = config.get('errors') or []
    if errors:
        lines.extend(['', 'Config errors:', *[f'- {error}' for error in errors]])
    live_probe = search.get('live_probe') if isinstance(search, dict) else None
    if live_probe:
        lines.extend(
            [
                '',
                'Search live probe:',
                f'- ok: {live_probe.get("ok")}',
                f'- provider: {live_probe.get("provider")}',
                f'- result count: {live_probe.get("result_count")}',
                f'- attempts: {live_probe.get("backend_attempts")}',
            ]
        )
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='Summarize local LM Studio research stack health.')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument('--research-dir', default=str(REPO_ROOT))
    parser.add_argument('--runs-root', default=str(REPO_ROOT / '.runtime' / 'research_runs'))
    parser.add_argument('--probe-tools', action='store_true', help='Launch the MCP server and list tools.')
    parser.add_argument('--probe-search', action='store_true', help='Run one live search-provider smoke test.')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate prompt, docs, config, and saved runs without launching the MCP server.',
    )
    parser.add_argument(
        '--refresh-prompt',
        action='store_true',
        help='Rewrite the generated LM Studio prompt file from docs before checking status.',
    )
    parser.add_argument('--json', action='store_true', help='Print machine-readable JSON.')
    args = parser.parse_args()

    status = build_status(
        config_path=Path(args.config).expanduser().resolve(),
        research_dir=Path(args.research_dir).expanduser().resolve(),
        runs_root=Path(args.runs_root).expanduser().resolve(),
        probe_tools=False if args.dry_run else args.probe_tools,
        probe_search=False if args.dry_run else args.probe_search,
        refresh_prompt=args.refresh_prompt,
    )
    if args.dry_run:
        status['dry_run'] = {'enabled': True, 'message': 'No MCP server process was launched.'}
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(format_status(status))
    return 0 if status.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
