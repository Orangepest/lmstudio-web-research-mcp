from __future__ import annotations

from urllib.parse import urlparse


HIGH_CREDIBILITY_NEWS = {
    'apnews.com',
    'bbc.com',
    'bbc.co.uk',
    'reuters.com',
}
LOW_CREDIBILITY_HOST_PARTS = (
    'pinterest.',
    'quora.com',
    'tumblr.com',
)
COMMUNITY_HOSTS = {
    'reddit.com',
    'news.ycombinator.com',
    'stackoverflow.com',
}


def normalize_domain(url_or_domain: str) -> str:
    value = str(url_or_domain or '').strip()
    parsed = urlparse(value if '://' in value else f'https://{value}')
    return (parsed.hostname or value).lower().removeprefix('www.')


def credibility_assessment(url: str, *, source_type: str | None = None) -> dict[str, object]:
    domain = normalize_domain(url)
    source_type = str(source_type or '')
    score = 50
    reasons: list[str] = []
    caveats: list[str] = []
    entity_class = 'general_web'

    if domain.endswith('.gov'):
        score += 35
        entity_class = 'government'
        reasons.append('Government domain.')
    elif domain.endswith('.edu'):
        score += 25
        entity_class = 'academic'
        reasons.append('Academic domain.')
    if domain in HIGH_CREDIBILITY_NEWS or any(domain.endswith(f'.{host}') for host in HIGH_CREDIBILITY_NEWS):
        score += 18
        entity_class = 'established_news'
        reasons.append('Established wire/news organization.')
    if domain == 'github.com' or domain == 'raw.githubusercontent.com' or domain.endswith('.github.io'):
        score += 16
        entity_class = 'repository'
        reasons.append('Repository or project-hosted source.')
    if domain.startswith('docs.') or source_type == 'documentation':
        score += 18
        entity_class = 'documentation'
        reasons.append('Documentation source.')
    if source_type in {'government', 'academic', 'repository', 'documentation'}:
        score += 8
        reasons.append(f'Source type classified as {source_type}.')
    elif source_type == 'news':
        score += 4
        reasons.append('News source.')
    elif source_type in {'blog', 'forum'}:
        score -= 12
        caveats.append(f'{source_type.title()} sources are usually supporting evidence, not final authority.')
    if domain in COMMUNITY_HOSTS or any(domain.endswith(f'.{host}') for host in COMMUNITY_HOSTS):
        score -= 18
        entity_class = 'community'
        caveats.append('Community/user-generated source.')
    if any(part in domain for part in LOW_CREDIBILITY_HOST_PARTS):
        score -= 20
        entity_class = 'low_authority'
        caveats.append('Low-authority or aggregation-heavy domain.')
    if not reasons:
        reasons.append('No strong domain-level authority signal found.')

    score = max(0, min(100, score))
    if score >= 80:
        label = 'high'
    elif score >= 60:
        label = 'medium'
    elif score >= 40:
        label = 'supporting'
    else:
        label = 'low'
    return {
        'domain': domain,
        'entity_class': entity_class,
        'score': score,
        'label': label,
        'reasons': reasons,
        'caveats': caveats,
    }
