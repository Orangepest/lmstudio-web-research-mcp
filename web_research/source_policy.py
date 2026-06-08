from __future__ import annotations

from urllib.parse import urlparse


SKIP_RESEARCH_DOMAINS = {
    'facebook.com',
    'm.facebook.com',
    'quora.com',
    'researchgate.net',
    'www.facebook.com',
    'www.quora.com',
    'www.researchgate.net',
}

SKIP_RESEARCH_DOMAIN_SUFFIXES = (
    '.facebook.com',
    '.quora.com',
    '.researchgate.net',
)

NO_RECOVERY_DOMAINS = {
    'facebook.com',
    'm.facebook.com',
    'quora.com',
    'researchgate.net',
    'www.facebook.com',
    'www.quora.com',
    'www.researchgate.net',
}

NO_RECOVERY_DOMAIN_SUFFIXES = (
    '.facebook.com',
    '.quora.com',
    '.researchgate.net',
)

PDF_VIEWER_PATH_MARKERS = (
    '/pdfjsviewer/',
    '/pdf.js/web/viewer.html',
)


def source_domain(url: str) -> str:
    return (urlparse(str(url or '')).hostname or '').lower().removeprefix('www.')


def domain_matches(domain: str, exact: set[str], suffixes: tuple[str, ...]) -> bool:
    normalized = str(domain or '').lower().removeprefix('www.')
    return normalized in {item.removeprefix('www.') for item in exact} or any(normalized.endswith(suffix.removeprefix('www.')) for suffix in suffixes)


def research_skip_reason(url: str) -> str | None:
    parsed = urlparse(str(url or ''))
    domain = source_domain(url)
    if domain_matches(domain, SKIP_RESEARCH_DOMAINS, SKIP_RESEARCH_DOMAIN_SUFFIXES):
        return 'hostile_or_low_value_research_domain'
    lowered_path = (parsed.path or '').lower()
    if any(marker in lowered_path for marker in PDF_VIEWER_PATH_MARKERS):
        return 'embedded_pdf_viewer_shell'
    return None


def should_attempt_recovery(url: str, *, block_marker: str | None = None, message: str | None = None) -> bool:
    domain = source_domain(url)
    if domain_matches(domain, NO_RECOVERY_DOMAINS, NO_RECOVERY_DOMAIN_SUFFIXES):
        return False
    lowered = f'{block_marker or ""} {message or ""}'.lower()
    if 'http 403' in lowered or 'http 429' in lowered:
        return False
    return True
