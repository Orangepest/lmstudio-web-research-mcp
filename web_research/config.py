from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from fnmatch import fnmatch
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    log_path: Path = Path(os.getenv('WEB_RESEARCH_LOG_PATH', '.runtime/web_research.log'))
    research_runs_dir: Path = Path(os.getenv('RESEARCH_RUNS_DIR', '.runtime/research_runs'))
    user_agent: str = os.getenv(
        'USER_AGENT',
        (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/133.0.0.0 Safari/537.36'
        ),
    )
    request_timeout: int = int(os.getenv('REQUEST_TIMEOUT', '25'))
    max_content_chars: int = int(os.getenv('MAX_CONTENT_CHARS', '120000'))
    fetch_domain_delay_seconds: float = float(os.getenv('FETCH_DOMAIN_DELAY_SECONDS', '0'))
    fetch_block_backoff_seconds: float = float(os.getenv('FETCH_BLOCK_BACKOFF_SECONDS', '30'))
    deep_research_soft_timeout_seconds: float = float(os.getenv('DEEP_RESEARCH_SOFT_TIMEOUT_SECONDS', '35'))
    cache_ttl_seconds: int = int(os.getenv('CACHE_TTL_SECONDS', '3600'))
    cache_max_items: int = int(os.getenv('CACHE_MAX_ITEMS', '256'))
    search_timeout: float = float(os.getenv('SEARCH_TIMEOUT', '4'))
    search_providers_raw: str = os.getenv(
        'SEARCH_PROVIDERS',
        'searxng_local_html,searxng_local,brave_html,duckduckgo_lite',
    )
    search_provider_backoff_seconds: float = float(os.getenv('SEARCH_PROVIDER_BACKOFF_SECONDS', '600'))
    searxng_engines: str = os.getenv('SEARXNG_ENGINES', 'google').strip()
    searxng_enabled_engines: str = os.getenv('SEARXNG_ENABLED_ENGINES', '').strip()
    searxng_disabled_engines: str = os.getenv('SEARXNG_DISABLED_ENGINES', '').strip()
    search_similar_cache: bool = os.getenv('SEARCH_SIMILAR_CACHE', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
    allowed_domains_raw: str = os.getenv('ALLOWED_DOMAINS', '')
    mcp_transport: str = os.getenv('MCP_TRANSPORT', 'streamable-http')
    mcp_host: str = os.getenv('MCP_HOST', '127.0.0.1')
    mcp_port: int = int(os.getenv('MCP_PORT', '8000'))
    mcp_mount_path: str = os.getenv('MCP_MOUNT_PATH', '/')
    mcp_sse_path: str = os.getenv('MCP_SSE_PATH', '/sse')
    mcp_message_path: str = os.getenv('MCP_MESSAGE_PATH', '/messages/')
    mcp_streamable_http_path: str = os.getenv('MCP_STREAMABLE_HTTP_PATH', '/mcp')
    browser_headless: bool = os.getenv('BROWSER_HEADLESS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
    browser_timeout_ms: int = int(os.getenv('BROWSER_TIMEOUT_MS', '30000'))
    browser_max_content_chars: int = int(os.getenv('BROWSER_MAX_CONTENT_CHARS', '60000'))
    browser_interaction: bool = os.getenv('BROWSER_INTERACTION', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
    browser_scroll_steps: int = int(os.getenv('BROWSER_SCROLL_STEPS', '4'))
    browser_executable_path: str = os.getenv('BROWSER_EXECUTABLE_PATH', '').strip()
    browser_locale: str = os.getenv('BROWSER_LOCALE', 'en-US').strip()
    browser_timezone_id: str = os.getenv('BROWSER_TIMEZONE_ID', 'Asia/Calcutta').strip()
    browser_profile_dir_override: Optional[str] = os.getenv('BROWSER_PROFILE_DIR', '').strip() or None
    browser_stealth_mode: bool = os.getenv('BROWSER_STEALTH_MODE', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
    mcp_compact_results: bool = os.getenv('MCP_COMPACT_RESULTS', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
    mcp_tool_profile: str = os.getenv('MCP_TOOL_PROFILE', 'safe').strip().lower()
    mcp_expose_advanced_tools: bool = os.getenv('MCP_EXPOSE_ADVANCED_TOOLS', 'false').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }
    mcp_result_excerpt_chars: int = int(os.getenv('MCP_RESULT_EXCERPT_CHARS', '12000'))
    mcp_result_max_items: int = int(os.getenv('MCP_RESULT_MAX_ITEMS', '8'))
    searxng_url: str = os.getenv('SEARXNG_URL', 'http://127.0.0.1:8888').strip().rstrip('/')
    local_llm_base_url: str = os.getenv('LOCAL_LLM_BASE_URL', 'http://127.0.0.1:1234/v1').strip().rstrip('/')
    local_llm_model: str = os.getenv('LOCAL_LLM_MODEL', 'auto').strip()
    local_llm_timeout: float = float(os.getenv('LOCAL_LLM_TIMEOUT', '8'))
    local_llm_contradiction_review: bool = os.getenv('LOCAL_LLM_CONTRADICTION_REVIEW', 'false').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }
    local_llm_report_synthesis: bool = os.getenv('LOCAL_LLM_REPORT_SYNTHESIS', 'false').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }
    local_llm_report_max_tokens: int = int(os.getenv('LOCAL_LLM_REPORT_MAX_TOKENS', '1800'))

    @property
    def allowed_domains(self) -> list[str]:
        return [item.strip().lower() for item in self.allowed_domains_raw.split(',') if item.strip()]

    @property
    def search_providers(self) -> list[str]:
        return [item.strip().lower() for item in self.search_providers_raw.split(',') if item.strip()]

    def is_domain_allowed(self, domain: str) -> bool:
        if not self.allowed_domains or self.allowed_domains == ['*']:
            return True
        return any(fnmatch(domain.lower(), pattern) for pattern in self.allowed_domains)

    def validate(self) -> None:
        if self.request_timeout <= 0:
            raise ValueError('REQUEST_TIMEOUT must be greater than 0')
        if self.max_content_chars <= 0:
            raise ValueError('MAX_CONTENT_CHARS must be greater than 0')
        if self.fetch_domain_delay_seconds < 0:
            raise ValueError('FETCH_DOMAIN_DELAY_SECONDS must not be negative')
        if self.fetch_block_backoff_seconds < 0:
            raise ValueError('FETCH_BLOCK_BACKOFF_SECONDS must not be negative')
        if self.deep_research_soft_timeout_seconds < 0:
            raise ValueError('DEEP_RESEARCH_SOFT_TIMEOUT_SECONDS must not be negative')
        if self.cache_ttl_seconds <= 0:
            raise ValueError('CACHE_TTL_SECONDS must be greater than 0')
        if self.cache_max_items <= 0:
            raise ValueError('CACHE_MAX_ITEMS must be greater than 0')
        if self.search_timeout <= 0:
            raise ValueError('SEARCH_TIMEOUT must be greater than 0')
        if self.search_provider_backoff_seconds < 0:
            raise ValueError('SEARCH_PROVIDER_BACKOFF_SECONDS must not be negative')
        if self.mcp_transport not in {'stdio', 'sse', 'streamable-http'}:
            raise ValueError("MCP_TRANSPORT must be one of: stdio, sse, streamable-http")
        if self.mcp_tool_profile not in {'safe', 'core', 'agent', 'agent_strict'}:
            raise ValueError("MCP_TOOL_PROFILE must be one of: safe, core, agent, agent_strict")
        if self.mcp_port <= 0:
            raise ValueError('MCP_PORT must be greater than 0')
        if self.browser_timeout_ms <= 0:
            raise ValueError('BROWSER_TIMEOUT_MS must be greater than 0')
        if self.browser_max_content_chars <= 0:
            raise ValueError('BROWSER_MAX_CONTENT_CHARS must be greater than 0')
        if self.browser_scroll_steps < 0:
            raise ValueError('BROWSER_SCROLL_STEPS must not be negative')
        if not self.browser_locale:
            raise ValueError('BROWSER_LOCALE must not be empty')
        if not self.browser_timezone_id:
            raise ValueError('BROWSER_TIMEZONE_ID must not be empty')
        if self.mcp_result_excerpt_chars <= 0:
            raise ValueError('MCP_RESULT_EXCERPT_CHARS must be greater than 0')
        if self.mcp_result_max_items <= 0:
            raise ValueError('MCP_RESULT_MAX_ITEMS must be greater than 0')
        if not self.local_llm_base_url:
            raise ValueError('LOCAL_LLM_BASE_URL must not be empty')
        if not self.local_llm_model:
            raise ValueError('LOCAL_LLM_MODEL must not be empty')
        if self.local_llm_timeout <= 0:
            raise ValueError('LOCAL_LLM_TIMEOUT must be greater than 0')
        if self.local_llm_report_max_tokens <= 0:
            raise ValueError('LOCAL_LLM_REPORT_MAX_TOKENS must be greater than 0')


settings = Settings()
settings.log_path.parent.mkdir(parents=True, exist_ok=True)
settings.research_runs_dir.mkdir(parents=True, exist_ok=True)
# Only create persistent profile dir if explicitly configured (for backwards compatibility)
if settings.browser_profile_dir_override:
    Path(settings.browser_profile_dir_override).mkdir(parents=True, exist_ok=True)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(configure_logging, '_configured', False):
        root.setLevel(level)
        return
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s')
    # Use RotatingFileHandler to cap log size at 10 MB with 5 backups (50 MB total)
    file_handler = RotatingFileHandler(
        settings.log_path,
        maxBytes=10_000_000,  # 10 MB
        backupCount=5,  # Keep up to 5 backup files (web_research.log.1, .log.2, etc.)
        encoding='utf-8',
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    configure_logging._configured = True
