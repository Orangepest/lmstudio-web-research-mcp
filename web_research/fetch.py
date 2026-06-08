from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import logging
import shutil
import tempfile
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from web_research.cache import cache
from web_research.config import settings
from web_research.extract import (
    ExtractedContent,
    classify_block_type,
    clean_text,
    detect_blocked_page,
    extract_html,
    extract_docx,
    extract_links,
    extract_pdf,
    summarize_text,
)
from web_research.rank import extract_evidence

logger = logging.getLogger(__name__)
_DOMAIN_NEXT_FETCH_AT: dict[str, float] = {}
_DOMAIN_BACKOFF_UNTIL: dict[str, float] = {}


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest() if text else ''


def _snapshot_metadata(
    *,
    fetched_at: str,
    final_url: str,
    status_code: int | None,
    content_type: str | None,
    title: str | None,
    content_hash: str,
    rendered: bool,
    text: str,
    requested_url: str | None = None,
    access_strategy: str = 'direct',
    links: list[dict[str, str]] | None = None,
    document_metadata: dict[str, Any] | None = None,
    browser_interactions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = {
        'fetched_at': fetched_at,
        'final_url': final_url,
        'status_code': status_code,
        'content_type': content_type,
        'title': title,
        'content_hash': content_hash,
        'rendered': rendered,
        'requested_url': requested_url or final_url,
        'access_strategy': access_strategy,
        'text_chars': len(text),
        'link_count': len(links or []),
    }
    if document_metadata:
        snapshot['document_metadata'] = document_metadata
    if browser_interactions:
        snapshot['browser_interactions'] = browser_interactions
    return snapshot


def _empty_snapshot(
    *,
    url: str,
    rendered: bool,
    fetched_at: str | None = None,
    requested_url: str | None = None,
    access_strategy: str = 'direct',
) -> dict[str, Any]:
    fetched = fetched_at or _utc_timestamp()
    return _snapshot_metadata(
        fetched_at=fetched,
        final_url=url,
        status_code=None,
        content_type=None,
        title=None,
        content_hash='',
        rendered=rendered,
        text='',
        requested_url=requested_url or url,
        access_strategy=access_strategy,
        links=[],
    )


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


def _is_docx_url(url: str, content_type: str | None = None) -> bool:
    parsed = urlparse(url)
    lowered_type = (content_type or '').lower()
    return parsed.path.lower().endswith('.docx') or 'wordprocessingml.document' in lowered_type


def _canonical_fetch_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.lower() == 'github.com':
        parts = [part for part in parsed.path.split('/') if part]
        if len(parts) >= 5 and parts[2] == 'blob':
            owner, repo, _blob, branch, *path_parts = parts
            path = '/'.join(path_parts)
            return f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}'
        if len(parts) == 2:
            owner, repo = parts
            return f'https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md'
    return url


async def _apply_domain_throttle(domain: str) -> dict[str, Any]:
    delay = max(0.0, float(settings.fetch_domain_delay_seconds))
    now = time.monotonic()
    target = max(_DOMAIN_NEXT_FETCH_AT.get(domain, 0.0), _DOMAIN_BACKOFF_UNTIL.get(domain, 0.0))
    wait_seconds = max(0.0, target - now)
    _DOMAIN_NEXT_FETCH_AT[domain] = max(now, target) + delay
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    return {
        'domain': domain,
        'wait_seconds': round(wait_seconds, 3),
        'delay_seconds': delay,
        'backoff_active': _DOMAIN_BACKOFF_UNTIL.get(domain, 0.0) > now,
    }


def _record_domain_backoff(domain: str) -> None:
    backoff = max(0.0, float(settings.fetch_block_backoff_seconds))
    if backoff <= 0:
        return
    _DOMAIN_BACKOFF_UNTIL[domain] = max(_DOMAIN_BACKOFF_UNTIL.get(domain, 0.0), time.monotonic() + backoff)


async def _wait_for_readable_content(page: Any, *, timeout_ms: int) -> None:
    selector_timeout = max(500, min(1500, timeout_ms // 6))
    for selector in ('main', 'article', '[role="main"]', '#content', '.content'):
        try:
            await page.locator(selector).first.wait_for(state='attached', timeout=selector_timeout)
            break
        except Exception:  # noqa: BLE001 - selector waits are best-effort readiness hints
            continue
    try:
        await page.wait_for_function(
            'document.body && document.body.innerText && document.body.innerText.trim().length > 200',
            timeout=max(1000, min(3000, timeout_ms // 3)),
        )
    except Exception as exc:  # noqa: BLE001 - body text readiness should not fail the fetch
        logger.debug('readable body wait timed out for %s: %s', page.url, exc)


async def _interact_with_page_for_research(page: Any, *, timeout_ms: int, scroll_steps: int) -> dict[str, Any]:
    interactions: dict[str, Any] = {
        'enabled': True,
        'dismissed_overlays': [],
        'scroll_steps': 0,
        'initial_text_chars': None,
        'final_text_chars': None,
    }
    try:
        dismissed = await page.evaluate(
            """() => {
                const patterns = [
                    /accept all/i, /accept cookies/i, /^accept$/i, /^agree$/i,
                    /i agree/i, /continue/i, /close/i, /dismiss/i, /got it/i
                ];
                const clicked = [];
                const candidates = Array.from(document.querySelectorAll(
                    'button, [role="button"], input[type="button"], input[type="submit"], a'
                ));
                for (const element of candidates) {
                    const text = (
                        element.innerText || element.value || element.getAttribute('aria-label') || ''
                    ).trim().slice(0, 80);
                    if (!text || !patterns.some((pattern) => pattern.test(text))) {
                        continue;
                    }
                    const rect = element.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) {
                        continue;
                    }
                    element.click();
                    clicked.push(text);
                    if (clicked.length >= 3) {
                        break;
                    }
                }
                return clicked;
            }"""
        )
        if isinstance(dismissed, list):
            interactions['dismissed_overlays'] = [str(item) for item in dismissed[:3]]
    except Exception as exc:  # noqa: BLE001 - page interaction is best-effort
        logger.debug('overlay dismissal failed for %s: %s', page.url, exc)

    try:
        initial_text_chars = await page.evaluate(
            "() => document.body && document.body.innerText ? document.body.innerText.trim().length : 0"
        )
        interactions['initial_text_chars'] = int(initial_text_chars or 0)
    except Exception as exc:  # noqa: BLE001 - metrics are best-effort
        logger.debug('initial text measurement failed for %s: %s', page.url, exc)

    wait_ms = max(100, min(750, timeout_ms // 20))
    total_steps = max(0, scroll_steps)
    for step in range(total_steps):
        try:
            await page.evaluate(
                """(input) => {
                    const height = Math.max(
                        document.body ? document.body.scrollHeight : 0,
                        document.documentElement ? document.documentElement.scrollHeight : 0
                    );
                    const target = Math.floor(height * ((input.step + 1) / Math.max(1, input.total)));
                    window.scrollTo(0, target);
                }""",
                {'step': step, 'total': total_steps},
            )
            interactions['scroll_steps'] = step + 1
            await page.wait_for_timeout(wait_ms)
        except Exception as exc:  # noqa: BLE001 - scrolling is best-effort
            logger.debug('lazy-load scroll failed for %s: %s', page.url, exc)
            break
    try:
        await page.evaluate('() => window.scrollTo(0, 0)')
    except Exception:  # noqa: BLE001 - scroll reset is cosmetic
        pass

    try:
        final_text_chars = await page.evaluate(
            "() => document.body && document.body.innerText ? document.body.innerText.trim().length : 0"
        )
        interactions['final_text_chars'] = int(final_text_chars or 0)
    except Exception as exc:  # noqa: BLE001 - metrics are best-effort
        logger.debug('final text measurement failed for %s: %s', page.url, exc)
    return interactions


async def read_url(url: str, query: str | None = None, render: bool = False, source_id: int = 1) -> dict[str, Any]:
    original_domain = validate_url(url)
    requested_url = _canonical_fetch_url(url)
    access_strategy = 'canonicalized' if requested_url != url else 'direct'
    cache_key = f'read:{url}:{query or ""}:{render}:{source_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return dict(cached, cached=True)
    try:
        payload = await _read_url_uncached(url, query=query, render=render, source_id=source_id)
    except BlockedPageError as exc:
        _record_domain_backoff(original_domain)
        fetched_at = _utc_timestamp()
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
            'fetched_at': fetched_at,
            'content_hash': '',
            'requested_url': requested_url,
            'access_strategy': access_strategy,
            'snapshot': _empty_snapshot(
                url=exc.url,
                rendered=exc.rendered,
                fetched_at=fetched_at,
                requested_url=requested_url,
                access_strategy=access_strategy,
            ),
        }
    except Exception as exc:
        logger.debug('Failed to read URL %s: %s', url, exc)
        fetched_at = _utc_timestamp()
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
            'rendered': render,
            'fetched_at': fetched_at,
            'content_hash': '',
            'requested_url': requested_url,
            'access_strategy': access_strategy,
            'snapshot': _empty_snapshot(
                url=url,
                rendered=render,
                fetched_at=fetched_at,
                requested_url=requested_url,
                access_strategy=access_strategy,
            ),
        }
    cache.set(cache_key, payload)
    return payload


async def _read_url_uncached(url: str, *, query: str | None, render: bool, source_id: int) -> dict[str, Any]:
    fetch_url = _canonical_fetch_url(url)
    fetch_domain = (urlparse(fetch_url).hostname or '').lower()
    access_strategy = 'canonicalized' if fetch_url != url else 'direct'
    throttle = await _apply_domain_throttle(fetch_domain)
    if render:
        payload = await _read_with_browser(
            fetch_url,
            query=query,
            source_id=source_id,
            original_url=url,
            access_strategy=access_strategy,
        )
        payload['throttle'] = throttle
        return payload
    headers = {'User-Agent': settings.user_agent}
    # Apply connection pool limits to prevent exhaustion under heavy load
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    with httpx.Client(timeout=settings.request_timeout, follow_redirects=True, limits=limits) as client:
        response = client.get(fetch_url, headers=headers)
        if response.status_code in {401, 403, 429}:
            if render:
                payload = await _read_with_browser(
                    fetch_url,
                    query=query,
                    source_id=source_id,
                    original_url=url,
                    access_strategy=access_strategy,
                )
                payload['throttle'] = throttle
                return payload
            raise BlockedPageError(f'HTTP {response.status_code}', url=str(response.url), rendered=False)
        response.raise_for_status()
    content_type = response.headers.get('content-type', '')
    final_url = str(response.url)
    if _is_pdf_url(final_url, content_type):
        extracted = extract_pdf(response.content)
        links: list[dict[str, str]] = []
    elif _is_docx_url(final_url, content_type):
        extracted = extract_docx(response.content)
        links = []
    elif 'html' in content_type or 'text/' in content_type or not content_type:
        html = response.text
        title_probe = extract_html(html, max_chars=2000).title
        block_marker = detect_blocked_page(html, title_probe)
        if block_marker:
            if render:
                payload = await _read_with_browser(
                    fetch_url,
                    query=query,
                    source_id=source_id,
                    original_url=url,
                    access_strategy=access_strategy,
                )
                payload['throttle'] = throttle
                return payload
            raise BlockedPageError(block_marker, url=final_url, rendered=False)
        extracted = extract_html(html)
        links = extract_links(html, final_url)
        if not extracted.text or (render and len(extracted.text) < 200):
            payload = await _read_with_browser(
                fetch_url,
                query=query,
                source_id=source_id,
                original_url=url,
                access_strategy=access_strategy,
            )
            payload['throttle'] = throttle
            return payload
    elif any(kind in content_type for kind in ('json', 'xml', 'csv', 'text')):
        text = response.text[:settings.max_content_chars].strip()
        extracted = ExtractedContent(title=None, text=text, content_hash=_content_hash(text))
        links = []
    else:
        raise ValueError(f'Unsupported content type: {content_type}')
    payload = _source_payload(
        url=url,
        final_url=final_url,
        status_code=response.status_code,
        content_type=content_type,
        title=extracted.title,
        text=extracted.text,
        content_hash=extracted.content_hash,
        links=links,
        query=query,
        source_id=source_id,
        rendered=False,
        requested_url=fetch_url,
        access_strategy=access_strategy,
        document_metadata=extracted.metadata,
    )
    payload['throttle'] = throttle
    return payload


async def _read_with_browser(
    url: str,
    *,
    query: str | None,
    source_id: int,
    original_url: str | None = None,
    access_strategy: str = 'direct',
) -> dict[str, Any]:
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
                await _wait_for_readable_content(page, timeout_ms=settings.browser_timeout_ms)
                browser_interactions: dict[str, Any] | None = None
                if settings.browser_interaction:
                    browser_interactions = await _interact_with_page_for_research(
                        page,
                        timeout_ms=settings.browser_timeout_ms,
                        scroll_steps=settings.browser_scroll_steps,
                    )
                    await _wait_for_readable_content(page, timeout_ms=settings.browser_timeout_ms)
                html = await page.content()
                page_title = await page.title()
                block_marker = detect_blocked_page(html, page_title)
                if block_marker:
                    raise BlockedPageError(block_marker, url=page.url, rendered=True)
                extracted = extract_html(html, max_chars=settings.browser_max_content_chars)
                if not extracted.text:
                    try:
                        body_text = clean_text(await page.locator('body').inner_text(timeout=1000))
                    except Exception as exc:  # noqa: BLE001 - fallback should never fail the whole read
                        logger.debug('body innerText fallback failed for %s: %s', url, exc)
                    else:
                        if body_text:
                            text = body_text[: settings.browser_max_content_chars].strip()
                            extracted = ExtractedContent(
                                title=extracted.title or page_title,
                                text=text,
                                content_hash=_content_hash(text),
                            )
                links = extract_links(html, page.url)
                return _source_payload(
                    url=original_url or url,
                    final_url=page.url,
                    status_code=response.status if response else None,
                    content_type='text/html; browser-rendered',
                    title=extracted.title or page_title,
                    text=extracted.text,
                    content_hash=extracted.content_hash,
                    links=links,
                    query=query,
                    source_id=source_id,
                    rendered=True,
                    requested_url=url,
                    access_strategy=access_strategy,
                    browser_interactions=browser_interactions,
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
    content_hash: str,
    links: list[dict[str, str]],
    query: str | None,
    source_id: int,
    rendered: bool,
    requested_url: str | None = None,
    access_strategy: str = 'direct',
    document_metadata: dict[str, Any] | None = None,
    browser_interactions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = extract_evidence(text, query, source_id=source_id, url=final_url, title=title)
    fetched_at = _utc_timestamp()
    content_hash = content_hash if text else ''
    message = 'Rendered page fetched' if rendered else 'URL fetched'
    if not text:
        message = 'No readable text extracted from rendered page' if rendered else 'No readable text extracted from URL'

    payload = {
        'ok': bool(text),
        'source_id': source_id,
        'url': url,
        'final_url': final_url,
        'status_code': status_code,
        'content_type': content_type,
        'title': title,
        'fetched_at': fetched_at,
        'content_hash': content_hash,
        'summary': summarize_text(text),
        'text': text,
        'evidence': evidence,
        'links': links,
        'message': message,
        'rendered': rendered,
        'requested_url': requested_url or final_url,
        'access_strategy': access_strategy,
        'cached': False,
        'snapshot': _snapshot_metadata(
            fetched_at=fetched_at,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            title=title,
            content_hash=content_hash,
            rendered=rendered,
            text=text,
            requested_url=requested_url or final_url,
            access_strategy=access_strategy,
            links=links,
            document_metadata=document_metadata,
            browser_interactions=browser_interactions,
        ),
    }
    if document_metadata:
        payload['document_metadata'] = document_metadata
    if browser_interactions:
        payload['browser_interactions'] = browser_interactions
    return payload


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
