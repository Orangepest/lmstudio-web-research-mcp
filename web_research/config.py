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
    browser_executable_path: str = os.getenv('BROWSER_EXECUTABLE_PATH', '').strip()
    browser_locale: str = os.getenv('BROWSER_LOCALE', 'en-US').strip()
    browser_timezone_id: str = os.getenv('BROWSER_TIMEZONE_ID', 'Asia/Calcutta').strip()
    browser_profile_dir_override: Optional[str] = os.getenv('BROWSER_PROFILE_DIR', '').strip() or None
    browser_stealth_mode: bool = os.getenv('BROWSER_STEALTH_MODE', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}

    @property
    def allowed_domains(self) -> list[str]:
        return [item.strip().lower() for item in self.allowed_domains_raw.split(',') if item.strip()]

    def is_domain_allowed(self, domain: str) -> bool:
        if not self.allowed_domains or self.allowed_domains == ['*']:
            return True
        return any(fnmatch(domain.lower(), pattern) for pattern in self.allowed_domains)

    def validate(self) -> None:
        if self.request_timeout <= 0:
            raise ValueError('REQUEST_TIMEOUT must be greater than 0')
        if self.max_content_chars <= 0:
            raise ValueError('MAX_CONTENT_CHARS must be greater than 0')
        if self.mcp_transport not in {'stdio', 'sse', 'streamable-http'}:
            raise ValueError("MCP_TRANSPORT must be one of: stdio, sse, streamable-http")
        if self.mcp_port <= 0:
            raise ValueError('MCP_PORT must be greater than 0')
        if self.browser_timeout_ms <= 0:
            raise ValueError('BROWSER_TIMEOUT_MS must be greater than 0')
        if self.browser_max_content_chars <= 0:
            raise ValueError('BROWSER_MAX_CONTENT_CHARS must be greater than 0')
        if not self.browser_locale:
            raise ValueError('BROWSER_LOCALE must not be empty')
        if not self.browser_timezone_id:
            raise ValueError('BROWSER_TIMEZONE_ID must not be empty')


settings = Settings()
settings.log_path.parent.mkdir(parents=True, exist_ok=True)
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
