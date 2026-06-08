from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup

from web_research.cache import cache
from web_research.config import settings

FRESHNESS_TO_DDG = {
    'day': 'd',
    'week': 'w',
    'month': 'm',
    'year': 'y',
}

KNOWN_SEARCH_PROVIDERS = {
    'searxng_local_html',
    'searxng_local',
    'mojeek_html',
    'brave_html',
    'duckduckgo_html',
    'duckduckgo_lite',
}

_PROVIDER_BACKOFF_UNTIL: dict[str, float] = {}

QUERY_SUFFIX_NORMALIZERS = (
    'official source',
    'official sources',
    'latest',
    'analysis',
    'data statistics',
    'statistics',
    'independent sources',
    'additional evidence',
    'more evidence',
    'supporting evidence',
)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme and parsed.netloc == '':
        return url
    path = parsed.path or '/'
    normalized = parsed._replace(fragment='', path=path).geturl()
    return normalized.rstrip('/') if path != '/' else normalized


def _unwrap_duckduckgo_url(href: str) -> str:
    parsed = urlparse(href)
    params = parse_qs(parsed.query)
    target = params.get('uddg', [''])[0]
    return normalize_url(target or href)


def _normalize_site(site: str | None) -> str | None:
    if not site:
        return None
    value = site.strip().lower()
    if not value:
        return None
    parsed = urlparse(value if '://' in value else f'https://{value}')
    host = (parsed.netloc or parsed.path).split('/')[0].split(':')[0].strip().lower()
    return host or None


def _host_matches_site(host: str, site: str | None) -> bool:
    normalized_site = _normalize_site(site)
    if not normalized_site:
        return True
    normalized_host = host.lower().split(':')[0].strip('.')
    if normalized_site.startswith('.'):
        suffix = normalized_site.lstrip('.')
        return normalized_host == suffix or normalized_host.endswith(f'.{suffix}')
    return normalized_host == normalized_site or normalized_host.endswith(f'.{normalized_site}')


def is_duckduckgo_challenge(html: str) -> bool:
    lowered = html.lower()
    return 'challenge-form' in lowered or 'anomaly.js' in lowered or 'duckduckgo.com/anomaly' in lowered


def parse_duckduckgo_results(html: str, limit: int, *, site: str | None = None) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    results: list[dict] = []
    seen: set[str] = set()
    for result_node in soup.select('.result, .web-result'):
        link = result_node.select_one('.result__a, a.result__url, a[href]')
        if link is None:
            continue
        title = link.get_text(' ', strip=True)
        href = link.get('href') or ''
        url = _unwrap_duckduckgo_url(href)
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'}:
            continue
        if not _host_matches_site(parsed.netloc, site):
            continue
        if not title or url in seen:
            continue
        snippet_node = result_node.select_one('.result__snippet, .snippet')
        snippet = snippet_node.get_text(' ', strip=True) if snippet_node else ''
        source_node = result_node.select_one('.result__url')
        source = source_node.get_text(' ', strip=True) if source_node else parsed.netloc
        results.append({'title': title, 'url': url, 'source': source, 'snippet': snippet, 'rank': len(results) + 1})
        seen.add(url)
        if len(results) >= limit:
            break
    return results


def parse_duckduckgo_lite_results(html: str, limit: int, *, site: str | None = None) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    results: list[dict] = []
    seen: set[str] = set()
    for link in soup.select('a.result-link[href], a[href]'):
        href = link.get('href') or ''
        url = _unwrap_duckduckgo_url(href)
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'}:
            continue
        if not _host_matches_site(parsed.netloc, site):
            continue
        if url in seen:
            continue
        title = link.get_text(' ', strip=True)
        if not title:
            continue
        snippet = ''
        row = link.find_parent('tr')
        if row:
            next_row = row.find_next_sibling('tr')
            if next_row:
                snippet_node = next_row.select_one('.result-snippet') or next_row
                snippet = snippet_node.get_text(' ', strip=True)
        results.append({'title': title, 'url': url, 'source': parsed.netloc, 'snippet': snippet, 'rank': len(results) + 1})
        seen.add(url)
        if len(results) >= limit:
            break
    return results


def parse_mojeek_results(html: str, limit: int, *, site: str | None = None) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    results: list[dict] = []
    seen: set[str] = set()
    for result_node in soup.select('li'):
        link = (
            result_node.select_one('h2 a.title[href]')
            or result_node.select_one('h2 a[href]')
            or result_node.select_one('a.ob[href]')
        )
        if link is None:
            continue
        href = link.get('href') or ''
        url = normalize_url(href)
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'}:
            continue
        if not _host_matches_site(parsed.netloc, site):
            continue
        if url in seen:
            continue
        title = link.get_text(' ', strip=True) or parsed.netloc
        snippet_node = result_node.select_one('p.s') or result_node.select_one('.b_caption p') or result_node.select_one('p')
        if snippet_node:
            snippet = snippet_node.get_text(' ', strip=True)
        else:
            snippet = result_node.get_text(' ', strip=True).replace(title, '', 1).strip(' -|')
        results.append({'title': title, 'url': url, 'source': parsed.netloc, 'snippet': snippet, 'rank': len(results) + 1})
        seen.add(url)
        if len(results) >= limit:
            break
    return results


def parse_brave_results(html: str, limit: int, *, site: str | None = None) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    results: list[dict] = []
    seen: set[str] = set()
    for result_node in soup.select('div.snippet[data-type="web"], div[data-type="web"]'):
        link = result_node.select_one('a[href]')
        if link is None:
            continue
        href = link.get('href') or ''
        url = normalize_url(href)
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'}:
            continue
        if 'search.brave.com' in parsed.netloc:
            continue
        if not _host_matches_site(parsed.netloc, site):
            continue
        if url in seen:
            continue
        title_node = result_node.select_one('.title, .search-snippet-title')
        title = title_node.get_text(' ', strip=True) if title_node else link.get_text(' ', strip=True)
        if not title:
            title = parsed.netloc
        snippet_node = result_node.select_one('.content, .generic-snippet')
        snippet = snippet_node.get_text(' ', strip=True) if snippet_node else ''
        results.append({'title': title, 'url': url, 'source': parsed.netloc, 'snippet': snippet, 'rank': len(results) + 1})
        seen.add(url)
        if len(results) >= limit:
            break
    return results


def parse_searxng_results(html: str, limit: int, *, site: str | None = None) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    results: list[dict] = []
    seen: set[str] = set()
    for result_node in soup.select('article.result'):
        link = result_node.select_one('h3 a[href]') or result_node.select_one('a.url_header[href]')
        if link is None:
            continue
        href = link.get('href') or ''
        url = normalize_url(href)
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'}:
            continue
        if not _host_matches_site(parsed.netloc, site):
            continue
        if url in seen:
            continue
        title = link.get_text(' ', strip=True) or parsed.netloc
        snippet_node = result_node.select_one('p.content')
        snippet = snippet_node.get_text(' ', strip=True) if snippet_node else ''
        engines = [node.get_text(' ', strip=True) for node in result_node.select('.engines span')]
        results.append(
            {
                'title': title,
                'url': url,
                'source': parsed.netloc,
                'snippet': snippet,
                'engines': engines,
                'rank': len(results) + 1,
            }
        )
        seen.add(url)
        if len(results) >= limit:
            break
    return results


def parse_searxng_json_results(text: str, limit: int, *, site: str | None = None) -> list[dict]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    raw_results = payload.get('results') if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return []
    results: list[dict] = []
    seen: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = normalize_url(str(item.get('url') or ''))
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'}:
            continue
        if not _host_matches_site(parsed.netloc, site):
            continue
        if url in seen:
            continue
        title = str(item.get('title') or parsed.netloc).strip()
        snippet = str(item.get('content') or item.get('snippet') or '').strip()
        engines = item.get('engines') if isinstance(item.get('engines'), list) else []
        results.append(
            {
                'title': title,
                'url': url,
                'source': parsed.netloc,
                'snippet': snippet,
                'engines': engines,
                'rank': len(results) + 1,
            }
        )
        seen.add(url)
        if len(results) >= limit:
            break
    return results


def _configured_search_providers() -> list[str]:
    providers = [provider for provider in settings.search_providers if provider in KNOWN_SEARCH_PROVIDERS]
    if providers:
        return providers
    return ['searxng_local_html', 'searxng_local', 'brave_html', 'duckduckgo_lite']


def _provider_backoff_remaining(provider: str, now: float | None = None) -> float:
    now = time.monotonic() if now is None else now
    until = _PROVIDER_BACKOFF_UNTIL.get(provider, 0)
    remaining = until - now
    if remaining <= 0:
        _PROVIDER_BACKOFF_UNTIL.pop(provider, None)
        return 0
    return remaining


def _remember_provider_failure(provider: str, message: str) -> None:
    lowered = message.lower()
    if settings.search_provider_backoff_seconds <= 0:
        return
    if '403' in lowered or '429' in lowered or 'timed out' in lowered or 'timeout' in lowered:
        _PROVIDER_BACKOFF_UNTIL[provider] = time.monotonic() + settings.search_provider_backoff_seconds


def _normalize_similar_search_query(query: str) -> str:
    normalized = ' '.join((query or '').lower().split())
    changed = True
    while changed:
        changed = False
        for suffix in QUERY_SUFFIX_NORMALIZERS:
            marker = f' {suffix}'
            if normalized.endswith(marker):
                normalized = normalized[: -len(marker)].strip()
                changed = True
    return normalized


def web_search(query: str, max_results: int = 10, freshness: str | None = None, site: str | None = None) -> dict:
    query = (query or '').strip()
    freshness = freshness.lower() if freshness else None
    site = _normalize_site(site)
    if not query:
        return {
            'ok': False,
            'query': query,
            'freshness': freshness,
            'site': site,
            'provider': None,
            'backend_attempts': [],
            'message': 'web search query must not be blank',
            'results': [],
            'cached': False,
        }

    max_results = max(1, min(max_results, 20))
    search_query = f'{query} site:{site}' if site else query
    params = {'q': search_query}
    if freshness in FRESHNESS_TO_DDG:
        params['df'] = FRESHNESS_TO_DDG[freshness]
    duckduckgo_url = f'https://duckduckgo.com/html/?{urlencode(params, quote_via=quote_plus)}'
    mojeek_url = f'https://www.mojeek.com/search?{urlencode({"q": search_query}, quote_via=quote_plus)}'
    brave_url = f'https://search.brave.com/search?{urlencode({"q": search_query, "source": "web"}, quote_via=quote_plus)}'
    cache_key = f'search:{search_query}:{freshness or ""}:{max_results}:{site or ""}'
    normalized_query = _normalize_similar_search_query(query)
    normalized_search_query = f'{normalized_query} site:{site}' if site and normalized_query else normalized_query
    normalized_cache_key = f'search-normalized:{normalized_search_query}:{freshness or ""}:{max_results}:{site or ""}' if normalized_search_query else ''

    cached = cache.get(cache_key)
    if cached is not None:
        return dict(cached, cached=True)
    if settings.search_similar_cache and normalized_cache_key and normalized_search_query != search_query:
        cached = cache.get(normalized_cache_key)
        if cached is not None:
            return dict(cached, cached=True, cache_match='normalized_query', original_query=query)

    errors: list[str] = []
    backend_attempts: list[dict] = []
    results: list[dict] = []
    provider = 'none'
    provider_order = _configured_search_providers()

    def record_attempt(
        provider_name: str,
        *,
        ok: bool,
        result_count: int = 0,
        latency_seconds: float = 0,
        message: str | None = None,
        url: str | None = None,
    ) -> None:
        attempt = {
            'provider': provider_name,
            'ok': ok,
            'result_count': result_count,
            'latency_seconds': round(latency_seconds, 3),
        }
        if message:
            attempt['message'] = message
        if url:
            attempt['url'] = url
        backend_attempts.append(attempt)

    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    with httpx.Client(timeout=settings.search_timeout, follow_redirects=True, limits=limits) as client:
        provider_urls = {
            'searxng_local': '',
            'searxng_local_html': '',
            'mojeek_html': mojeek_url,
            'brave_html': brave_url,
            'duckduckgo_html': duckduckgo_url,
            'duckduckgo_lite': f'https://lite.duckduckgo.com/lite/?{urlencode(params, quote_via=quote_plus)}',
        }
        if settings.searxng_url:
            searxng_params = {'q': search_query, 'format': 'json'}
            searxng_html_params = {'q': search_query}
            if freshness in {'day', 'week', 'month', 'year'}:
                searxng_params['time_range'] = freshness
                searxng_html_params['time_range'] = freshness
            if settings.searxng_engines:
                searxng_params['engines'] = settings.searxng_engines
                searxng_html_params['engines'] = settings.searxng_engines
            elif settings.searxng_enabled_engines:
                searxng_params['enabled_engines'] = settings.searxng_enabled_engines
                searxng_html_params['enabled_engines'] = settings.searxng_enabled_engines
            elif settings.searxng_disabled_engines:
                searxng_params['disabled_engines'] = settings.searxng_disabled_engines
                searxng_html_params['disabled_engines'] = settings.searxng_disabled_engines
            provider_urls['searxng_local'] = f'{settings.searxng_url}/search?{urlencode(searxng_params, quote_via=quote_plus)}'
            provider_urls['searxng_local_html'] = f'{settings.searxng_url}/search?{urlencode(searxng_html_params, quote_via=quote_plus)}'

        def parse_provider(provider_name: str, text: str) -> list[dict]:
            if provider_name == 'searxng_local':
                parsed = parse_searxng_json_results(text, max_results, site=site)
                return parsed or parse_searxng_results(text, max_results, site=site)
            if provider_name == 'searxng_local_html':
                return parse_searxng_results(text, max_results, site=site)
            if provider_name == 'mojeek_html':
                return parse_mojeek_results(text, max_results, site=site)
            if provider_name == 'brave_html':
                return parse_brave_results(text, max_results, site=site)
            if provider_name == 'duckduckgo_html':
                if is_duckduckgo_challenge(text):
                    raise ValueError('duckduckgo_html returned a challenge page')
                return parse_duckduckgo_results(text, max_results, site=site)
            if provider_name == 'duckduckgo_lite':
                if is_duckduckgo_challenge(text):
                    raise ValueError('duckduckgo_lite returned a challenge page')
                return parse_duckduckgo_lite_results(text, max_results, site=site)
            return []

        for provider_name in provider_order:
            if results:
                break
            provider = provider_name
            url = provider_urls.get(provider_name) or ''
            if provider_name.startswith('searxng') and not url:
                message = f'{provider_name} skipped: SEARXNG_URL is not configured'
                errors.append(message)
                record_attempt(provider_name, ok=False, message=message)
                continue
            backoff_remaining = _provider_backoff_remaining(provider_name)
            if backoff_remaining > 0:
                message = f'{provider_name} skipped: provider in backoff for {round(backoff_remaining, 1)}s'
                record_attempt(provider_name, ok=False, message=message, url=url)
                continue
            started = time.monotonic()
            try:
                response = client.get(url, headers={'User-Agent': settings.user_agent})
                response.raise_for_status()
                results = parse_provider(provider_name, response.text)
                if not results:
                    message = f'{provider_name} returned no parseable results'
                    errors.append(message)
                    record_attempt(provider_name, ok=False, result_count=0, latency_seconds=time.monotonic() - started, message=message, url=url)
                else:
                    record_attempt(provider_name, ok=True, result_count=len(results), latency_seconds=time.monotonic() - started, url=url)
            except (httpx.HTTPError, ValueError) as exc:
                message = f'{provider_name} failed: {exc}'
                errors.append(message)
                _remember_provider_failure(provider_name, message)
                record_attempt(provider_name, ok=False, latency_seconds=time.monotonic() - started, message=message, url=url)

    if not results:
        return {
            'ok': False,
            'query': query,
            'freshness': freshness,
            'site': site,
            'provider': provider,
            'provider_order': provider_order,
            'backend_attempts': backend_attempts,
            'message': '; '.join(errors) or 'web search returned no results',
            'results': [],
            'cached': False,
        }
    payload = {
        'ok': True,
        'query': query,
        'freshness': freshness,
        'site': site,
        'provider': provider,
        'provider_order': provider_order,
        'backend_attempts': backend_attempts,
        'results': results,
        'warnings': errors,
        'cached': False,
    }
    cache.set(cache_key, payload)
    if settings.search_similar_cache and normalized_cache_key:
        cache.set(normalized_cache_key, payload)
    return payload
