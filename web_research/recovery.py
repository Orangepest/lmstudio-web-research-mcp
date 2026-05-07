from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


@dataclass(frozen=True)
class RecoveryCandidate:
    url: str
    strategy: str
    reason: str


def _replace_query(url: str, additions: dict[str, str]) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.update(additions)
    return urlunparse(parsed._replace(query=urlencode(params)))


def _replace_path(url: str, path: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=path, query='', fragment=''))


def _origin_url(url: str, path: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))


def build_recovery_candidates(url: str, *, limit: int = 8) -> list[RecoveryCandidate]:
    """
    Build safe alternate source URLs for a blocked page.

    These candidates only try commonly exposed alternate representations from the
    same domain. They do not solve captchas, rotate proxies, or bypass access
    controls.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return []

    path = parsed.path or '/'
    candidates: list[RecoveryCandidate] = []

    def add(candidate_url: str, strategy: str, reason: str) -> None:
        normalized = urlunparse(urlparse(candidate_url)._replace(fragment=''))
        if normalized != url and all(item.url != normalized for item in candidates):
            candidates.append(RecoveryCandidate(url=normalized, strategy=strategy, reason=reason))

    add(_replace_query(url, {'output': '1'}), 'print_query', 'Try a common print-friendly query parameter')
    add(_replace_query(url, {'view': 'print'}), 'print_query', 'Try a common print view query parameter')
    add(_replace_query(url, {'amp': '1'}), 'amp_query', 'Try a common AMP query parameter')

    clean_path = path.rstrip('/') or '/'
    if clean_path != '/':
        add(_replace_path(url, f'{clean_path}/print'), 'print_path', 'Try a common print URL path')
        add(_replace_path(url, f'{clean_path}/amp'), 'amp_path', 'Try a common AMP URL path')
        if '.' not in clean_path.rsplit('/', 1)[-1]:
            add(_replace_path(url, f'{clean_path}.pdf'), 'pdf_path', 'Try a same-path PDF representation')

    for feed_path, strategy, reason in (
        ('/sitemap.xml', 'sitemap', 'Try the same-domain sitemap for alternate public URLs'),
        ('/rss', 'rss', 'Try a same-domain RSS feed'),
        ('/feed', 'rss', 'Try a same-domain feed endpoint'),
        ('/feed.xml', 'rss', 'Try a same-domain Atom/RSS feed'),
    ):
        add(_origin_url(url, feed_path), strategy, reason)

    return candidates[: max(0, limit)]
