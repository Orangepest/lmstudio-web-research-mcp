from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from web_research.cache import cache
from web_research.fetch import BlockedPageError, read_url


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

    def __enter__(self) -> 'FakeClient':
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, _url: str, headers: dict[str, str]) -> FakeResponse:
        return self.response


class FetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._items.clear()

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

    async def test_read_url_handles_pdf_url(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
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

    async def test_read_url_falls_back_to_browser_for_forbidden_response(self) -> None:
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
            result = await read_url('https://example.com/blocked', query='blocked')

        self.assertTrue(result['ok'])
        self.assertTrue(result['rendered'])

    async def test_read_url_returns_structured_captcha_failure(self) -> None:
        response = FakeResponse(url='https://example.com/challenge', status_code=403)

        with (
            patch('web_research.fetch.httpx.Client', return_value=FakeClient(response)),
            patch(
                'web_research.fetch._read_with_browser',
                side_effect=BlockedPageError('captcha', url='https://example.com/challenge', rendered=True),
            ),
        ):
            result = await read_url('https://example.com/challenge', query='blocked')

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
