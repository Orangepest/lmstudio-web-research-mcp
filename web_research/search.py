from __future__ import annotations

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
        if site and site.lower() not in parsed.netloc.lower():
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
        if site and site.lower() not in parsed.netloc.lower():
            continue
        if url in seen:
            continue
        title = link.get_text(' ', strip=True) or parsed.netloc
        snippet_node = result_node.select_one('p.s') or result_node.select_one('.b_caption p') or result_node.select_one('p')
        if snippet_node:
            snippet = snippet_node.get_text(' ', strip=True)
        else:
            snippet = result_node.get_text(' ', strip=True).replace(title, '', 1).strip(' -|')
        results.append(
            {
                'title': title,
                'url': url,
                'source': parsed.netloc,
                'snippet': snippet,
                'rank': len(results) + 1,
            }
        )
        seen.add(url)
        if len(results) >= limit:
            break
    return results


def web_search(query: str, max_results: int = 10, freshness: str | None = None, site: str | None = None) -> dict:
    max_results = max(1, min(max_results, 20))
    freshness = freshness.lower() if freshness else None
    search_query = f'{query} site:{site}' if site else query
    params = {'q': search_query}
    if freshness in FRESHNESS_TO_DDG:
        params['df'] = FRESHNESS_TO_DDG[freshness]
    duckduckgo_url = f'https://duckduckgo.com/html/?{urlencode(params, quote_via=quote_plus)}'
    mojeek_url = f'https://www.mojeek.com/search?{urlencode({"q": search_query}, quote_via=quote_plus)}'
    cache_key = f'search:{search_query}:{freshness or ""}:{max_results}:{site or ""}'
    
    # Check in-memory cache first
    cached = cache.get(cache_key)
    if cached is not None:
        return dict(cached, cached=True)
    
    errors: list[str] = []
    results: list[dict] = []
    provider = 'mojeek_html'
    # Apply connection pool limits to prevent exhaustion under heavy load
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    with httpx.Client(timeout=settings.request_timeout, follow_redirects=True, limits=limits) as client:
        try:
            response = client.get(mojeek_url, headers={'User-Agent': settings.user_agent})
            response.raise_for_status()
            results = parse_mojeek_results(response.text, max_results, site=site)
            if not results:
                errors.append('mojeek_html returned no parseable results')
        except httpx.HTTPError as exc:
            errors.append(f'mojeek_html failed: {exc}')

        if not results:
            provider = 'duckduckgo_html'
            try:
                response = client.get(duckduckgo_url, headers={'User-Agent': settings.user_agent})
                response.raise_for_status()
                if is_duckduckgo_challenge(response.text):
                    errors.append('duckduckgo_html returned a challenge page')
                else:
                    results = parse_duckduckgo_results(response.text, max_results, site=site)
                    if not results:
                        errors.append('duckduckgo_html returned no parseable results')
            except httpx.HTTPError as exc:
                errors.append(f'duckduckgo_html failed: {exc}')

        if not results:
            provider = 'duckduckgo_lite'
            lite_url = f'https://lite.duckduckgo.com/lite/?{urlencode(params, quote_via=quote_plus)}'
            try:
                response = client.get(lite_url, headers={'User-Agent': settings.user_agent})
                response.raise_for_status()
                if is_duckduckgo_challenge(response.text):
                    errors.append('duckduckgo_lite returned a challenge page')
                else:
                    results = parse_duckduckgo_results(response.text, max_results, site=site)
                    if not results:
                        errors.append('duckduckgo_lite returned no parseable results')
            except httpx.HTTPError as exc:
                errors.append(f'duckduckgo_lite failed: {exc}')

    if not results:
        return {
            'ok': False,
            'query': query,
            'freshness': freshness,
            'site': site,
            'provider': provider,
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
        'results': results,
        'warnings': errors,
        'cached': False,
    }
    cache.set(cache_key, payload)
    return payload
