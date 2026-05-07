from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from web_research.fetch import read_url
from web_research.recovery import build_recovery_candidates
from web_research.search import normalize_url, web_search


def _manual_handoff(url: str) -> dict[str, str]:
    return {
        'url': url,
        'message': 'Open this page manually if you are authorized to access it. Complete any required site check in your browser, then retry with BROWSER_PROFILE_DIR set to that browser profile.',
    }


def _same_domain(url: str, domain: str) -> bool:
    return (urlparse(url).hostname or '').lower() == domain


async def research_web(
    query: str,
    max_results: int = 8,
    read_top: int = 4,
    freshness: str | None = None,
    site: str | None = None,
    render: bool = False,
) -> dict[str, Any]:
    max_results = max(1, min(max_results, 20))
    read_top = max(1, min(read_top, max_results))
    search_payload = web_search(query=query, max_results=max_results, freshness=freshness, site=site)
    results = search_payload.get('results', [])
    sources: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seen: set[str] = set()
    blocked_domains: set[str] = set()
    source_id = 1
    for result in results:
        if len(sources) >= read_top:
            break
        url = normalize_url(result['url'])
        domain = (urlparse(url).hostname or '').lower()
        if url in seen:
            continue
        if domain in blocked_domains:
            failures.append(
                {
                    'url': url,
                    'title': result.get('title'),
                    'message': f'skipped after repeated blocking from {domain}',
                    'blocked': True,
                    'block_type': 'blocked',
                    'manual_handoff': _manual_handoff(url),
                }
            )
            continue
        seen.add(url)
        payload = await read_url(url, query=query, render=render, source_id=source_id)
        if payload.get('ok'):
            source = dict(payload)
            source['search_result'] = result
            sources.append(source)
            source_id += 1
        else:
            message = payload.get('message', 'read failed')
            lowered = message.lower()
            if domain and (
                payload.get('blocked')
                or any(marker in lowered for marker in ('blocked', 'captcha', 'challenge', 'forbidden', 'enable javascript'))
            ):
                blocked_domains.add(domain)
            failure = {'url': url, 'title': result.get('title'), 'message': message}
            if payload.get('blocked'):
                failure.update(
                    {
                        'blocked': True,
                        'block_type': payload.get('block_type', 'blocked'),
                        'block_marker': payload.get('block_marker'),
                        'manual_handoff': _manual_handoff(url),
                        'recovery_attempts': [],
                    }
                )
                for candidate in build_recovery_candidates(url):
                    candidate_url = normalize_url(candidate.url)
                    if candidate_url in seen or not _same_domain(candidate_url, domain):
                        continue
                    seen.add(candidate_url)
                    recovery_payload = await read_url(candidate_url, query=query, render=render, source_id=source_id)
                    attempt = {
                        'url': candidate_url,
                        'strategy': candidate.strategy,
                        'reason': candidate.reason,
                        'ok': bool(recovery_payload.get('ok')),
                    }
                    if recovery_payload.get('blocked'):
                        attempt['blocked'] = True
                        attempt['block_type'] = recovery_payload.get('block_type', 'blocked')
                    elif not recovery_payload.get('ok'):
                        attempt['message'] = recovery_payload.get('message', 'read failed')
                    failure['recovery_attempts'].append(attempt)
                    if recovery_payload.get('ok'):
                        source = dict(recovery_payload)
                        source['search_result'] = result
                        source['recovered_from'] = {
                            'url': url,
                            'strategy': candidate.strategy,
                            'reason': candidate.reason,
                        }
                        sources.append(source)
                        source_id += 1
                        break
            failures.append(failure)
    evidence = []
    for source in sources:
        evidence.extend(source.get('evidence', []))
    evidence.sort(key=lambda item: (item.get('rank', 999), item.get('source_id', 999)))
    return {
        'ok': bool(sources),
        'query': query,
        'freshness': freshness,
        'site': site,
        'render': render,
        'search': search_payload,
        'sources': sources,
        'evidence': evidence,
        'citations': [item['citation'] for item in evidence],
        'failures': failures,
        'blocked_sources': [failure for failure in failures if failure.get('blocked')],
        'manual_visit_links': [
            failure['manual_handoff']
            for failure in failures
            if failure.get('blocked') and failure.get('manual_handoff')
        ],
        'message': 'Research completed with sources' if sources else 'Search completed but no sources could be read',
    }
