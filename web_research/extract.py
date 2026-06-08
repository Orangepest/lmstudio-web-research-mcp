from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pypdf import PdfReader
from xml.etree import ElementTree

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
    metadata: dict[str, object] = field(default_factory=dict)


BLOCK_TAGS = {'p', 'li', 'blockquote', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table'}
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


def extract_table_text(table: object) -> str:
    rows: list[list[str]] = []
    try:
        table_rows = table.select('tr')  # type: ignore[attr-defined]
    except AttributeError:
        return ''
    for row in table_rows:
        cells = [clean_text(cell.get_text(' ', strip=True)) for cell in row.select('th, td')]
        cells = [cell for cell in cells if cell]
        if cells:
            rows.append(cells)
    if not rows:
        return ''

    width = max(len(row) for row in rows)
    normalized = [row + [''] * (width - len(row)) for row in rows]
    lines = [' | '.join(row).strip() for row in normalized]
    if len(normalized) > 1 and table.select_one('th'):  # type: ignore[attr-defined]
        separator = ' | '.join(['---'] * width)
        lines.insert(1, separator)
    return 'Table:\n' + '\n'.join(lines)


def clean_pdf_page_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in (text or '').splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if re.search(r'[ \t]{3,}', stripped):
            line = re.sub(r'[ \t]{3,}', ' | ', stripped)
        else:
            line = clean_text(stripped)
        if line:
            lines.append(line)
    return '\n'.join(lines)


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
        if node_name == 'table':
            text = extract_table_text(node)
        else:
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
    extracted_pages = 0
    for index, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text(extraction_mode='layout') or ''
        except Exception:
            try:
                raw_text = page.extract_text() or ''
            except Exception:
                raw_text = ''
        page_text = clean_pdf_page_text(raw_text)
        if page_text:
            extracted_pages += 1
            parts.append(f'Page {index}: {page_text}')
    text = '\n'.join(parts)[: max_chars or settings.max_content_chars].strip()
    title = None
    metadata: dict[str, object] = {
        'document_type': 'pdf',
        'page_count': len(reader.pages),
        'extracted_page_count': extracted_pages,
        'encrypted': bool(getattr(reader, 'is_encrypted', False)),
    }
    if reader.metadata:
        raw_metadata = reader.metadata
        for key, value in {
            'title': raw_metadata.title,
            'author': raw_metadata.author,
            'subject': raw_metadata.subject,
            'creator': raw_metadata.creator,
            'producer': raw_metadata.producer,
        }.items():
            if value:
                metadata[key] = str(value)
    try:
        metadata['outline_item_count'] = len(reader.outline or [])
    except Exception:
        metadata['outline_item_count'] = 0
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title)
    return ExtractedContent(title=title, text=text, content_hash=_hash_text(text), metadata=metadata)


def _docx_text_nodes(root: ElementTree.Element) -> list[str]:
    namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    values = []
    for node in root.iter(f'{namespace}t'):
        if node.text:
            values.append(node.text)
    return values


def extract_docx(data: bytes, *, max_chars: int | None = None) -> ExtractedContent:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document_xml = archive.read('word/document.xml')
        root = ElementTree.fromstring(document_xml)
        namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        paragraphs = []
        table_count = 0
        for child in root.iter(f'{namespace}body'):
            for element in list(child):
                if element.tag == f'{namespace}p':
                    text = clean_text(''.join(_docx_text_nodes(element)))
                    if text:
                        paragraphs.append(text)
                elif element.tag == f'{namespace}tbl':
                    table_count += 1
                    rows = []
                    for row in element.iter(f'{namespace}tr'):
                        cells = []
                        for cell in row.iter(f'{namespace}tc'):
                            cell_text = clean_text(' '.join(_docx_text_nodes(cell)))
                            if cell_text:
                                cells.append(cell_text)
                        if cells:
                            rows.append(' | '.join(cells))
                    if rows:
                        paragraphs.append('Table:\n' + '\n'.join(rows))
            break
        text = '\n'.join(paragraphs)[: max_chars or settings.max_content_chars].strip()
        metadata: dict[str, object] = {
            'document_type': 'docx',
            'paragraph_count': len([item for item in paragraphs if not item.startswith('Table:\n')]),
            'table_count': table_count,
        }
        title = None
        try:
            props = ElementTree.fromstring(archive.read('docProps/core.xml'))
            for node in props.iter():
                tag = node.tag.rsplit('}', 1)[-1]
                if tag in {'title', 'creator', 'subject'} and node.text:
                    key = 'author' if tag == 'creator' else tag
                    metadata[key] = clean_text(node.text)
                    if tag == 'title':
                        title = clean_text(node.text)
        except (KeyError, ElementTree.ParseError):
            pass
    return ExtractedContent(title=title, text=text, content_hash=_hash_text(text), metadata=metadata)


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
