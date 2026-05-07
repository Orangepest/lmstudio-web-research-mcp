from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from typing import Any
from urllib.parse import urlparse

import httpx

from web_research.cache import cache
from web_research.config import settings
from web_research.extract import (
    ExtractedContent,
    classify_block_type,
    detect_blocked_page,
    extract_html,
    extract_links,
    extract_pdf,
    summarize_text,
)
from web_research.rank import extract_evidence

logger = logging.getLogger(__name__)


class BlockedPageError(RuntimeError):
    def __init__(self, marker: str, *, url: str, rendered: bool) -> None:
        self.marker = marker
        self.block_type = classify_block_type(marker)
        self.url = url
        self.rendered = rendered
        super().__init__(f'Page appears blocked by {self.block_type} or anti-bot challenge: {marker}')


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'}:
        raise ValueError('Only http/https URLs are allowed')
    domain = (parsed.hostname or '').lower()
    if not settings.is_domain_allowed(domain):
        raise ValueError(f"Domain '{domain}' is not in ALLOWED_DOMAINS")
    return domain


def _is_pdf_url(url: str, content_type: str | None = None) -> bool:
    parsed = urlparse(url)
    return parsed.path.lower().endswith('.pdf') or 'pdf' in (content_type or '').lower()


async def read_url(url: str, query: str | None = None, render: bool = False, source_id: int = 1) -> dict[str, Any]:
    validate_url(url)
    cache_key = f'read:{url}:{query or ""}:{render}:{source_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return dict(cached, cached=True)
    try:
        payload = await _read_url_uncached(url, query=query, render=render, source_id=source_id)
    except BlockedPageError as exc:
        payload = {
            'ok': False,
            'url': url,
            'final_url': exc.url,
            'status_code': None,
            'content_type': None,
            'title': None,
            'summary': '',
            'text': '',
            'evidence': [],
            'links': [],
            'message': str(exc),
            'cached': False,
            'blocked': True,
            'block_type': exc.block_type,
            'block_marker': exc.marker,
            'rendered': exc.rendered,
        }
    except Exception as exc:
        logger.debug('Failed to read URL %s: %s', url, exc)
        payload = {
            'ok': False,
            'url': url,
            'final_url': url,
            'status_code': None,
            'content_type': None,
            'title': None,
            'summary': '',
            'text': '',
            'evidence': [],
            'links': [],
            'message': str(exc),
            'cached': False,
            'blocked': False,
        }
    cache.set(cache_key, payload)
    return payload


async def _read_url_uncached(url: str, *, query: str | None, render: bool, source_id: int) -> dict[str, Any]:
    if render:
        return await _read_with_browser(url, query=query, source_id=source_id)
    headers = {'User-Agent': settings.user_agent}
    # Apply connection pool limits to prevent exhaustion under heavy load
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    with httpx.Client(timeout=settings.request_timeout, follow_redirects=True, limits=limits) as client:
        response = client.get(url, headers=headers)
        if response.status_code in {401, 403, 429}:
            return await _read_with_browser(url, query=query, source_id=source_id)
        response.raise_for_status()
    content_type = response.headers.get('content-type', '')
    final_url = str(response.url)
    if _is_pdf_url(final_url, content_type):
        extracted = extract_pdf(response.content)
        links: list[dict[str, str]] = []
    elif 'html' in content_type or 'text/' in content_type or not content_type:
        html = response.text
        title_probe = extract_html(html, max_chars=2000).title
        block_marker = detect_blocked_page(html, title_probe)
        if block_marker:
            return await _read_with_browser(url, query=query, source_id=source_id)
        extracted = extract_html(html)
        links = extract_links(html, final_url)
        if len(extracted.text) < 200:
            return await _read_with_browser(url, query=query, source_id=source_id)
    elif any(kind in content_type for kind in ('json', 'xml', 'csv', 'text')):
        text = response.text[:settings.max_content_chars].strip()
        extracted = ExtractedContent(title=None, text=text, content_hash='')
        links = []
    else:
        raise ValueError(f'Unsupported content type: {content_type}')
    return _source_payload(
        url=url,
        final_url=final_url,
        status_code=response.status_code,
        content_type=content_type,
        title=extracted.title,
        text=extracted.text,
        links=links,
        query=query,
        source_id=source_id,
        rendered=False,
    )


async def _read_with_browser(url: str, *, query: str | None, source_id: int) -> dict[str, Any]:
    """
    Read URL with browser rendering using ephemeral profiles for isolation.
    
    If BROWSER_PROFILE_DIR is set, uses that persistent path for backwards compatibility.
    Otherwise, creates a unique temp directory per request and cleans it up after use.
    
    Supports stealth mode (BROWSER_STEALTH_MODE) to minimize detection/blocks during research.
    """
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError('Playwright is not installed. Run "python -m playwright install chromium".') from exc

    # Determine profile directory: use override if configured, otherwise ephemeral temp
    if settings.browser_profile_dir_override:
        profile_dir = settings.browser_profile_dir_override
        cleanup_profile = False  # Don't clean up persistent profiles
    else:
        profile_dir = tempfile.mkdtemp(prefix='playwright_profile_')
        cleanup_profile = True  # Clean up ephemeral profiles after use

    try:
        executable_path = settings.browser_executable_path or None
        async with async_playwright() as playwright:
            # Browser launch arguments for stealth mode
            launch_args = {
                'headless': settings.browser_headless,
                'executable_path': executable_path,
            }
            
            # Add stealth-specific arguments to minimize detection
            if settings.browser_stealth_mode:
                launch_args['args'] = [
                    '--disable-blink-features=AutomationControlled',  # Hide automation detection
                    '--disable-dev-shm-usage',  # Reduce memory usage
                    '--no-sandbox',  # Reduce sandboxing overhead
                    '--disable-gpu',  # Disable GPU (faster on headless)
                    '--disable-web-resources',  # Reduce resource tracking
                ]
            
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                **launch_args,
                locale=settings.browser_locale,
                timezone_id=settings.browser_timezone_id,
                viewport={'width': 1440, 'height': 960},
                user_agent=settings.user_agent,
            )
            
            page = context.pages[0] if context.pages else await context.new_page()
            
            # Apply stealth JS injection to hide automation
            if settings.browser_stealth_mode:
                await page.add_init_script('''
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5],  // Fake plugins
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'],
                    });
                    window.chrome = {
                        runtime: {},
                    };
                    Object.defineProperty(document, 'hidden', {
                        get: () => false,
                    });
                    Object.defineProperty(document, 'visibilityState', {
                        get: () => 'visible',
                    });
                ''')
            
            try:
                response = await page.goto(url, wait_until='domcontentloaded', timeout=settings.browser_timeout_ms)
                try:
                    await page.wait_for_load_state('networkidle', timeout=max(1000, settings.browser_timeout_ms // 2))
                except PlaywrightTimeoutError:
                    logger.debug('networkidle wait timed out for %s', url)
                html = await page.content()
                page_title = await page.title()
                block_marker = detect_blocked_page(html, page_title)
                if block_marker:
                    raise BlockedPageError(block_marker, url=page.url, rendered=True)
                extracted = extract_html(html, max_chars=settings.browser_max_content_chars)
                links = extract_links(html, page.url)
                return _source_payload(
                    url=url,
                    final_url=page.url,
                    status_code=response.status if response else None,
                    content_type='text/html; browser-rendered',
                    title=extracted.title or page_title,
                    text=extracted.text,
                    links=links,
                    query=query,
                    source_id=source_id,
                    rendered=True,
                )
            finally:
                await page.close()
                await context.close()
    finally:
        # Clean up ephemeral temp profiles to prevent disk bloat
        if cleanup_profile and profile_dir:
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
            except Exception as e:
                logger.debug('Failed to clean up temp profile directory %s: %s', profile_dir, e)


def _source_payload(
    *,
    url: str,
    final_url: str,
    status_code: int | None,
    content_type: str | None,
    title: str | None,
    text: str,
    links: list[dict[str, str]],
    query: str | None,
    source_id: int,
    rendered: bool,
) -> dict[str, Any]:
    evidence = extract_evidence(text, query, source_id=source_id, url=final_url, title=title)
    
    return {
        'ok': bool(text),
        'source_id': source_id,
        'url': url,
        'final_url': final_url,
        'status_code': status_code,
        'content_type': content_type,
        'title': title,
        'summary': summarize_text(text),
        'text': text,
        'evidence': evidence,
        'links': links,
        'message': 'Rendered page fetched' if rendered else 'URL fetched',
        'rendered': rendered,
        'cached': False,
    }


async def discover_links(
    url: str,
    query: str | None = None,
    render: bool = False,
    file_types: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    page = await read_url(url=url, query=query, render=render, source_id=1)
    links = page.get('links', [])
    file_type_filter = {item.lower().lstrip('.') for item in (file_types or []) if item}
    if file_type_filter:
        links = [link for link in links if link.get('file_type') in file_type_filter]
    if query:
        terms = [term.lower() for term in query.split() if term.strip()]
        if terms:
            links = [
                link for link in links
                if any(term in f"{link.get('text', '')} {link.get('url', '')}".lower() for term in terms)
            ]
    return {
        'ok': page.get('ok', False),
        'url': url,
        'final_url': page.get('final_url', url),
        'title': page.get('title'),
        'links': links[: max(1, min(limit, 100))],
        'source_summary': page.get('summary', ''),
        'message': page.get('message', ''),
    }
