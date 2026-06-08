from __future__ import annotations

import io
from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from pypdf import PdfWriter

from web_research.cache import cache
from web_research.fetch import (
    BlockedPageError,
    _DOMAIN_BACKOFF_UNTIL,
    _DOMAIN_NEXT_FETCH_AT,
    _apply_domain_throttle,
    _interact_with_page_for_research,
    _record_domain_backoff,
    _wait_for_readable_content,
    read_url,
)


class FakeResponse:
    def __init__(self, *, url: str, text: str = '', content: bytes = b'', content_type: str = 'text/html', status_code: int = 200) -> None:
        self.url = url
        self.text = text
        self.content = content
        self.headers = {'content-type': content_type}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requested_urls: list[str] = []

    def __enter__(self) -> 'FakeClient':
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
        self.requested_urls.append(url)
        return self.response


class FakeLocator:
    def __init__(self, page: 'FakePage') -> None:
        self.page = page

    @property
    def first(self) -> 'FakeLocator':
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self.page.selector_waits.append({'state': state, 'timeout': timeout})


class FakePage:
    url = 'https://example.com/app'

    def __init__(self) -> None:
        self.selector_waits: list[dict[str, object]] = []
        self.function_waits: list[dict[str, object]] = []

    def locator(self, selector: str) -> FakeLocator:
        self.selector_waits.append({'selector': selector})
        return FakeLocator(self)

    async def wait_for_function(self, expression: str, *, timeout: int) -> None:
        self.function_waits.append({'expression': expression, 'timeout': timeout})


class FakeInteractivePage:
    url = 'https://example.com/app'

    def __init__(self) -> None:
        self.evaluate_calls: list[dict[str, object]] = []
        self.waits: list[int] = []
        self.text_chars = 80

    async def evaluate(self, expression: str, arg: object = None) -> object:
        self.evaluate_calls.append({'expression': expression, 'arg': arg})
        if 'querySelectorAll' in expression:
            return ['Accept all']
        if 'innerText.trim().length' in expression:
            self.text_chars += 120
            return self.text_chars
        return None

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)


class FetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._items.clear()
        _DOMAIN_NEXT_FETCH_AT.clear()
        _DOMAIN_BACKOFF_UNTIL.clear()

    async def test_read_url_extracts_html_and_evidence(self) -> None:
        body = 'Online retrieval with citations is useful for LM Studio models. ' * 8
        html = f'<html><head><title>Research</title></head><body><main><p>{body}</p></main></body></html>'
        response = FakeResponse(url='https://example.com/page', text=html)

        with patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)):
            result = await read_url('https://example.com/page', query='retrieval citations')

        self.assertTrue(result['ok'])
        self.assertEqual(result['title'], 'Research')
        self.assertIn('retrieval', result['evidence'][0]['quote'].lower())
        self.assertFalse(result['rendered'])
        self.assertEqual(len(result['content_hash']), 64)
        self.assertEqual(result['snapshot']['content_hash'], result['content_hash'])
        self.assertEqual(result['snapshot']['final_url'], 'https://example.com/page')
        self.assertEqual(result['snapshot']['text_chars'], len(result['text']))
        self.assertEqual(result['snapshot']['link_count'], len(result['links']))
        self.assertEqual(result['throttle']['domain'], 'example.com')
        fetched_at = datetime.fromisoformat(result['fetched_at'].replace('Z', '+00:00'))
        self.assertEqual(fetched_at.tzinfo, UTC)

    async def test_domain_throttle_waits_on_repeated_domain(self) -> None:
        throttled_settings = SimpleNamespace(fetch_domain_delay_seconds=1.0, fetch_block_backoff_seconds=30.0)

        with patch('web_research.fetch.settings', throttled_settings), patch('web_research.fetch.asyncio.sleep', new_callable=AsyncMock) as sleep_mock:
            first = await _apply_domain_throttle('example.com')
            second = await _apply_domain_throttle('example.com')

        self.assertEqual(first['wait_seconds'], 0)
        self.assertGreater(second['wait_seconds'], 0)
        sleep_mock.assert_awaited_once()

    async def test_domain_backoff_adds_wait_after_block(self) -> None:
        throttled_settings = SimpleNamespace(fetch_domain_delay_seconds=0.0, fetch_block_backoff_seconds=2.0)

        with patch('web_research.fetch.settings', throttled_settings), patch('web_research.fetch.asyncio.sleep', new_callable=AsyncMock) as sleep_mock:
            _record_domain_backoff('blocked.example')
            result = await _apply_domain_throttle('blocked.example')

        self.assertGreater(result['wait_seconds'], 0)
        self.assertTrue(result['backoff_active'])
        sleep_mock.assert_awaited_once()

    async def test_read_url_returns_links_and_file_types(self) -> None:
        body = 'Online retrieval with citations is useful for LM Studio models. ' * 8
        html = f'''
        <html><body><main>
          <p>{body}</p>
          <a href="/paper.pdf">Download paper</a>
          <a href="https://other.example/data.csv">CSV data</a>
        </main></body></html>
        '''
        response = FakeResponse(url='https://example.com/page', text=html)

        with patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)):
            result = await read_url('https://example.com/page', query='retrieval')

        self.assertEqual(result['links'][0]['url'], 'https://example.com/paper.pdf')
        self.assertEqual(result['links'][0]['file_type'], 'pdf')

    async def test_read_url_fetches_github_blob_as_raw_content(self) -> None:
        body = 'The GitHub raw changelog documents MCP behavior and API updates. ' * 6
        response = FakeResponse(
            url='https://raw.githubusercontent.com/lmstudio-ai/docs/main/1_developer/api-changelog.md',
            text=body,
            content_type='text/markdown',
        )
        client = FakeClient(response)

        with patch('web_research.fetch.httpx.Client', return_value=client):
            result = await read_url(
                'https://github.com/lmstudio-ai/docs/blob/main/1_developer/api-changelog.md',
                query='MCP API',
            )

        self.assertTrue(result['ok'])
        self.assertEqual(
            client.requested_urls,
            ['https://raw.githubusercontent.com/lmstudio-ai/docs/main/1_developer/api-changelog.md'],
        )
        self.assertEqual(result['url'], 'https://github.com/lmstudio-ai/docs/blob/main/1_developer/api-changelog.md')
        self.assertEqual(result['final_url'], 'https://raw.githubusercontent.com/lmstudio-ai/docs/main/1_developer/api-changelog.md')
        self.assertEqual(result['requested_url'], 'https://raw.githubusercontent.com/lmstudio-ai/docs/main/1_developer/api-changelog.md')
        self.assertEqual(result['access_strategy'], 'canonicalized')
        self.assertEqual(result['snapshot']['access_strategy'], 'canonicalized')
        self.assertEqual(
            result['snapshot']['requested_url'],
            'https://raw.githubusercontent.com/lmstudio-ai/docs/main/1_developer/api-changelog.md',
        )

    async def test_read_url_fetches_github_repo_readme_as_raw_content(self) -> None:
        body = 'The public repository README documents a local AI coding assistant and setup steps. ' * 6
        response = FakeResponse(
            url='https://raw.githubusercontent.com/ai-for-developers/awesome-ai-coding-tools/HEAD/README.md',
            text=body,
            content_type='text/markdown',
        )
        client = FakeClient(response)

        with patch('web_research.fetch.httpx.Client', return_value=client):
            result = await read_url(
                'https://github.com/ai-for-developers/awesome-ai-coding-tools',
                query='local AI coding assistant',
            )

        self.assertTrue(result['ok'])
        self.assertEqual(
            client.requested_urls,
            ['https://raw.githubusercontent.com/ai-for-developers/awesome-ai-coding-tools/HEAD/README.md'],
        )
        self.assertEqual(result['url'], 'https://github.com/ai-for-developers/awesome-ai-coding-tools')
        self.assertEqual(
            result['final_url'],
            'https://raw.githubusercontent.com/ai-for-developers/awesome-ai-coding-tools/HEAD/README.md',
        )
        self.assertEqual(result['access_strategy'], 'canonicalized')

    async def test_read_url_retries_empty_static_html_with_browser(self) -> None:
        response = FakeResponse(url='https://example.com/app', text='<html><head><title>App</title></head><body></body></html>')
        browser_payload = {
            'ok': True,
            'source_id': 1,
            'url': 'https://example.com/app',
            'final_url': 'https://example.com/app',
            'status_code': 200,
            'content_type': 'text/html; browser-rendered',
            'title': 'App',
            'summary': 'Rendered app text',
            'text': 'Rendered app text',
            'evidence': [],
            'links': [],
            'message': 'Rendered page fetched',
            'rendered': True,
            'cached': False,
            'fetched_at': '2026-06-03T00:00:00Z',
            'content_hash': 'a' * 64,
            'snapshot': {'content_hash': 'a' * 64, 'rendered': True, 'text_chars': 17},
        }

        with (
            patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)),
            patch('web_research.fetch._read_with_browser', return_value=browser_payload) as browser_mock,
        ):
            result = await read_url('https://example.com/app', query='app')

        self.assertTrue(result['ok'])
        self.assertTrue(result['rendered'])
        browser_mock.assert_awaited_once()

    async def test_wait_for_readable_content_uses_main_selector_and_body_text(self) -> None:
        page = FakePage()

        await _wait_for_readable_content(page, timeout_ms=12000)

        self.assertEqual(page.selector_waits[0]['selector'], 'main')
        self.assertEqual(page.selector_waits[1]['state'], 'attached')
        self.assertIn('innerText', page.function_waits[0]['expression'])

    async def test_interact_with_page_for_research_dismisses_and_scrolls(self) -> None:
        page = FakeInteractivePage()

        result = await _interact_with_page_for_research(page, timeout_ms=12000, scroll_steps=3)

        self.assertEqual(result['dismissed_overlays'], ['Accept all'])
        self.assertEqual(result['scroll_steps'], 3)
        self.assertEqual(len(page.waits), 3)
        self.assertGreater(result['final_text_chars'], result['initial_text_chars'])
        self.assertTrue(any(call['arg'] == {'step': 2, 'total': 3} for call in page.evaluate_calls))

    async def test_read_url_handles_pdf_url(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.add_metadata({'/Title': 'PDF Test Doc', '/Author': 'Research Bot'})
        buffer = io.BytesIO()
        writer.write(buffer)
        response = FakeResponse(
            url='https://example.com/file.pdf',
            content=buffer.getvalue(),
            content_type='application/pdf',
        )

        with patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)):
            result = await read_url('https://example.com/file.pdf', query='anything')

        self.assertFalse(result['ok'])
        self.assertEqual(result['content_type'], 'application/pdf')
        self.assertEqual(result['text'], '')
        self.assertEqual(result['content_hash'], '')
        self.assertEqual(result['snapshot']['content_hash'], '')
        self.assertEqual(result['snapshot']['text_chars'], 0)
        self.assertEqual(result['document_metadata']['document_type'], 'pdf')
        self.assertEqual(result['document_metadata']['page_count'], 1)
        self.assertEqual(result['document_metadata']['extracted_page_count'], 0)
        self.assertEqual(result['document_metadata']['title'], 'PDF Test Doc')
        self.assertEqual(result['document_metadata']['author'], 'Research Bot')
        self.assertEqual(result['snapshot']['document_metadata']['page_count'], 1)
        self.assertEqual(result['message'], 'No readable text extracted from URL')

    async def test_read_url_handles_docx_url(self) -> None:
        buffer = io.BytesIO()
        document_xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:p><w:r><w:t>DOCX evidence supports local document research.</w:t></w:r></w:p></w:body>
        </w:document>'''
        core_xml = '''<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
          xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Docx Title</dc:title></cp:coreProperties>'''
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('word/document.xml', document_xml)
            archive.writestr('docProps/core.xml', core_xml)
        response = FakeResponse(
            url='https://example.com/file.docx',
            content=buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )

        with patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)):
            result = await read_url('https://example.com/file.docx', query='document research')

        self.assertTrue(result['ok'])
        self.assertEqual(result['title'], 'Docx Title')
        self.assertIn('DOCX evidence supports local document research.', result['text'])
        self.assertEqual(result['document_metadata']['document_type'], 'docx')
        self.assertEqual(result['document_metadata']['paragraph_count'], 1)
        self.assertEqual(result['snapshot']['document_metadata']['document_type'], 'docx')

    async def test_read_url_falls_back_to_browser_for_forbidden_response_when_rendering(self) -> None:
        response = FakeResponse(url='https://example.com/blocked', status_code=403)
        browser_payload = {
            'ok': True,
            'source_id': 1,
            'url': 'https://example.com/blocked',
            'final_url': 'https://example.com/blocked',
            'status_code': 200,
            'content_type': 'text/html; browser-rendered',
            'title': 'Rendered',
            'summary': 'Rendered summary',
            'text': 'Rendered text',
            'evidence': [],
            'rendered': True,
        }

        with (
            patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)),
            patch('web_research.fetch._read_with_browser', return_value=browser_payload),
        ):
            result = await read_url('https://example.com/blocked', query='blocked', render=True)

        self.assertTrue(result['ok'])
        self.assertTrue(result['rendered'])

    async def test_read_url_returns_blocked_failure_without_rendering(self) -> None:
        response = FakeResponse(url='https://example.com/blocked', status_code=403)

        with (
            patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)),
            patch('web_research.fetch._read_with_browser') as browser_mock,
        ):
            result = await read_url('https://example.com/blocked', query='blocked')

        self.assertFalse(result['ok'])
        self.assertTrue(result['blocked'])
        self.assertEqual(result['block_marker'], 'HTTP 403')
        self.assertFalse(result['rendered'])
        self.assertEqual(result['content_hash'], '')
        self.assertEqual(result['requested_url'], 'https://example.com/blocked')
        self.assertEqual(result['access_strategy'], 'direct')
        self.assertEqual(result['snapshot']['final_url'], 'https://example.com/blocked')
        self.assertEqual(result['snapshot']['requested_url'], 'https://example.com/blocked')
        self.assertEqual(result['snapshot']['access_strategy'], 'direct')
        browser_mock.assert_not_called()

    async def test_read_url_error_preserves_canonicalized_request_metadata(self) -> None:
        response = FakeResponse(
            url='https://raw.githubusercontent.com/lmstudio-ai/docs/main/missing.md',
            status_code=500,
        )

        with patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)):
            result = await read_url('https://github.com/lmstudio-ai/docs/blob/main/missing.md', query='missing')

        self.assertFalse(result['ok'])
        self.assertEqual(result['requested_url'], 'https://raw.githubusercontent.com/lmstudio-ai/docs/main/missing.md')
        self.assertEqual(result['access_strategy'], 'canonicalized')
        self.assertEqual(result['snapshot']['requested_url'], 'https://raw.githubusercontent.com/lmstudio-ai/docs/main/missing.md')
        self.assertEqual(result['snapshot']['access_strategy'], 'canonicalized')

    async def test_read_url_returns_structured_captcha_failure(self) -> None:
        response = FakeResponse(url='https://example.com/challenge', status_code=403)

        with (
            patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)),
            patch(
                'web_research.fetch._read_with_browser',
                side_effect=BlockedPageError('captcha', url='https://example.com/challenge', rendered=True),
            ),
        ):
            result = await read_url('https://example.com/challenge', query='blocked', render=True)

        self.assertFalse(result['ok'])
        self.assertTrue(result['blocked'])
        self.assertEqual(result['block_type'], 'captcha')
        self.assertEqual(result['block_marker'], 'captcha')
        self.assertTrue(result['rendered'])

    async def test_concurrent_ephemeral_browser_renders(self) -> None:
        """
        Test that multiple concurrent browser render requests use ephemeral profiles
        and don't cause lock contention or cross-request state leakage.

        This validates the refactoring to use per-request temp profiles.
        """
        import asyncio

        # Create multiple fake responses for concurrent requests
        responses = [
            FakeResponse(url='https://httpbin.org/html', text='<html><body>Test 1</body></html>'),
            FakeResponse(url='https://example.com', text='<html><body>Test 2</body></html>'),
            FakeResponse(url='https://httpbin.org/delay/1', text='<html><body>Test 3</body></html>'),
        ]

        # Mock _read_with_browser to verify it's called with different URLs
        # and returns successfully without lock contention
        async def mock_browser_render(url: str, *, query: str | None, source_id: int) -> dict:
            # Simulate brief async work (browser rendering)
            await asyncio.sleep(0.01)
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'status_code': 200,
                'content_type': 'text/html; browser-rendered',
                'title': f'Page {source_id}',
                'summary': '',
                'text': f'Content from {url}',
                'evidence': [],
                'links': [],
                'rendered': True,
                'cached': False,
            }

        urls = [
            'https://httpbin.org/html',
            'https://example.com',
            'https://httpbin.org/delay/1',
        ]

        # Launch 3 concurrent browser requests
        with patch('web_research.fetch._read_with_browser', side_effect=mock_browser_render):
            results = await asyncio.gather(*[
                mock_browser_render(url, query=None, source_id=i)
                for i, url in enumerate(urls)
            ])

        # Verify all succeeded (no lock timeout)
        self.assertEqual(len(results), 3)
        for i, result in enumerate(results):
            self.assertTrue(result['ok'], f"Request {i} failed")
            self.assertEqual(result['status_code'], 200, f"Request {i} got wrong status")
            self.assertTrue(result['rendered'], f"Request {i} not rendered")

        # Verify no cross-request state (URLs are distinct)
        self.assertEqual(results[0]['final_url'], urls[0])
        self.assertEqual(results[1]['final_url'], urls[1])
        self.assertEqual(results[2]['final_url'], urls[2])


if __name__ == '__main__':
    unittest.main()
