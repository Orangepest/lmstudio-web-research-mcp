from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pypdf import PdfReader

from web_research.config import settings

# Common boilerplate tags and classes to remove during distillation
BOILERPLATE_TAGS = {
    'nav', 'header', 'footer', 'aside', 'noscript',
    'script', 'style', 'svg', 'canvas', 'iframe'
}
BOILERPLATE_CLASSES = {
    'navbar', 'nav-bar', 'navigation', 'menu', 'sidebar',
    'footer', 'header', 'breadcrumb', 'pagination',
    'advertisement', 'ads', 'ad-', 'sponsor', 'promo',
    'cookie', 'cookies', 'privacy', 'consent',
    'comment', 'comments', 'related', 'recommended',
    'trending', 'popular', 'similar', 'social',
}
BOILERPLATE_IDS = {
    'header', 'footer', 'sidebar', 'nav', 'navbar',
    'menu', 'navigation', 'breadcrumb', 'ads', 'advertisement'
}


@dataclass
class ExtractedContent:
    title: str | None
    text: str
    content_hash: str


BLOCK_TAGS = {'p', 'li', 'blockquote', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'td', 'th'}
HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
BLOCK_INDICATORS = (
    'captcha',
    'verify you are human',
    'press and hold',
    'access denied',
    'temporarily blocked',
    'unusual traffic',
    'enable javascript and cookies',
    'sorry, you have been blocked',
    'challenge-platform',
    'cf-challenge',
)

CAPTCHA_INDICATORS = {
    'captcha',
    'verify you are human',
    'press and hold',
    'unusual traffic',
    'challenge-platform',
    'cf-challenge',
}


def clean_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def summarize_text(text: str, max_sentences: int = 3, max_chars: int = 420) -> str:
    sentences = [clean_text(part) for part in re.split(r'(?<=[.!?])\s+', text.replace('\n', ' ')) if clean_text(part)]
    summary = ' '.join(sentences[:max_sentences]).strip() or clean_text(text[:max_chars])
    return summary[:max_chars].rstrip()


def detect_blocked_page(html: str, title: str | None = None) -> str | None:
    combined = f'{title or ""}\n{html}'.lower()
    for marker in BLOCK_INDICATORS:
        if marker in combined:
            return marker
    return None


def classify_block_type(marker: str | None) -> str:
    if not marker:
        return 'blocked'
    return 'captcha' if marker.lower() in CAPTCHA_INDICATORS else 'blocked'


def distill_html(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Remove HTML boilerplate (nav, ads, footers, etc.) to reduce noise.
    
    Distillation improves LLM comprehension by removing:
    - Navigation and menus (nav, header, footer, aside)
    - Advertisements and sponsored content
    - Cookie/privacy notices
    - Related/recommended content widgets
    - Comments and social widgets
    
    Returns: Modified BeautifulSoup object (original is modified).
    """
    # Remove boilerplate tags
    for tag in soup(BOILERPLATE_TAGS):
        tag.decompose()
    
    # Remove elements with boilerplate classes/IDs
    # Use a list() copy to avoid modifying collection during iteration
    for element in list(soup.find_all(True)):
        # Skip if element is None or has been removed
        if element is None or element.parent is None:
            continue
        
        try:
            elem_id = (element.get('id') or '').lower()
            elem_class = ' '.join(element.get('class') or []).lower()
        except (AttributeError, TypeError):
            continue
        
        # Check if ID matches boilerplate patterns
        if elem_id and any(b in elem_id for b in BOILERPLATE_IDS):
            try:
                element.decompose()
            except Exception:
                pass
            continue
        
        # Check if class matches boilerplate patterns
        if elem_class:
            should_remove = False
            if any(b in elem_class for b in BOILERPLATE_CLASSES):
                should_remove = True
            # Remove data attributes commonly used for tracking/ads
            elif 'ad' in elem_class or 'tracker' in elem_class or 'analytics' in elem_class:
                should_remove = True
            
            if should_remove:
                try:
                    element.decompose()
                except Exception:
                    pass
    
    return soup


def extract_html(html: str, *, max_chars: int | None = None) -> ExtractedContent:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg', 'canvas', 'iframe']):
        tag.decompose()
    
    # Apply boilerplate distillation to remove noise
    soup = distill_html(soup)
    
    title = soup.title.get_text(' ', strip=True) if soup.title else None
    main = soup.find('main') or soup.find('article') or soup.body or soup
    pieces: list[str] = []
    seen: set[str] = set()
    current_heading: str | None = None
    for node in main.descendants:
        node_name = getattr(node, 'name', None)
        if node_name not in BLOCK_TAGS:
            continue
        text = clean_text(node.get_text(' ', strip=True))
        if not text:
            continue
        if node_name in HEADING_TAGS:
            current_heading = text
            entry = f'# {text}'
        elif current_heading and text != current_heading and not text.lower().startswith(current_heading.lower()):
            entry = f'{current_heading}: {text}'
        else:
            entry = text
        if entry not in seen:
            pieces.append(entry)
            seen.add(entry)
    if not pieces:
        pieces = [clean_text(main.get_text(' ', strip=True))]
    text = '\n'.join(piece for piece in pieces if piece)[: max_chars or settings.max_content_chars].strip()
    return ExtractedContent(title=title, text=text, content_hash=_hash_text(text))


def extract_pdf(data: bytes, *, max_chars: int | None = None) -> ExtractedContent:
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        page_text = clean_text(page.extract_text() or '')
        if page_text:
            parts.append(f'Page {index}: {page_text}')
    text = '\n'.join(parts)[: max_chars or settings.max_content_chars].strip()
    title = None
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title)
    return ExtractedContent(title=title, text=text, content_hash=_hash_text(text))


def extract_links(html: str, base_url: str, *, limit: int = 100) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, 'html.parser')
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in soup.select('a[href]'):
        href = node.get('href') or ''
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'}:
            continue
        normalized = parsed._replace(fragment='').geturl()
        if normalized in seen:
            continue
        text = clean_text(node.get_text(' ', strip=True))[:180]
        path = parsed.path.lower()
        file_type = ''
        for suffix in ('.pdf', '.csv', '.json', '.xml', '.txt', '.md', '.doc', '.docx', '.xls', '.xlsx'):
            if path.endswith(suffix):
                file_type = suffix.lstrip('.')
                break
        links.append({'url': normalized, 'text': text, 'domain': parsed.netloc.lower(), 'file_type': file_type})
        seen.add(normalized)
        if len(links) >= limit:
            break
    return links


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()
