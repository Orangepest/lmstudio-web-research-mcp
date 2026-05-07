from __future__ import annotations

import unittest
from unittest.mock import patch

from web_research.service import research_web


class ResearchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_research_web_returns_partial_results_and_failures(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'A', 'url': 'https://example.com/a', 'source': 'example.com', 'snippet': 'A', 'rank': 1},
                {'title': 'A dup', 'url': 'https://example.com/a#section', 'source': 'example.com', 'snippet': 'A', 'rank': 2},
                {'title': 'B', 'url': 'https://example.com/b', 'source': 'example.com', 'snippet': 'B', 'rank': 3},
            ],
        }

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            if url.endswith('/b'):
                return {'ok': False, 'url': url, 'message': 'blocked'}
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'status_code': 200,
                'content_type': 'text/html',
                'title': 'A',
                'summary': 'Summary',
                'text': 'Evidence text',
                'evidence': [{'source_id': source_id, 'url': url, 'title': 'A', 'quote': 'Evidence text', 'char_range': [0, 13], 'citation': f'source:{source_id}[0:13]', 'rank': 1}],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=3, read_top=2)

        self.assertTrue(result['ok'])
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(len(result['failures']), 1)
        self.assertEqual(result['citations'], ['source:1[0:13]'])

    async def test_research_web_skips_domain_after_blocking(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'Blocked A', 'url': 'https://blocked.example/a', 'source': 'blocked.example', 'snippet': '', 'rank': 1},
                {'title': 'Blocked B', 'url': 'https://blocked.example/b', 'source': 'blocked.example', 'snippet': '', 'rank': 2},
                {'title': 'Readable', 'url': 'https://readable.example/c', 'source': 'readable.example', 'snippet': '', 'rank': 3},
            ],
        }
        calls: list[str] = []

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            calls.append(url)
            if 'blocked.example' in url:
                return {'ok': False, 'url': url, 'message': 'Browser session appears blocked or challenged: captcha'}
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'status_code': 200,
                'content_type': 'text/html',
                'title': 'Readable',
                'summary': 'Summary',
                'text': 'Evidence text',
                'evidence': [],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=3, read_top=1)

        self.assertTrue(result['ok'])
        self.assertEqual(calls, ['https://blocked.example/a', 'https://readable.example/c'])
        self.assertIn('skipped after repeated blocking', result['failures'][1]['message'])

    async def test_research_web_preserves_structured_block_reason(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'Blocked', 'url': 'https://blocked.example/a', 'source': 'blocked.example', 'snippet': '', 'rank': 1},
                {'title': 'Readable', 'url': 'https://readable.example/b', 'source': 'readable.example', 'snippet': '', 'rank': 2},
            ],
        }

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            if 'blocked.example' in url:
                return {
                    'ok': False,
                    'url': url,
                    'message': 'Page appears blocked by captcha or anti-bot challenge: captcha',
                    'blocked': True,
                    'block_type': 'captcha',
                    'block_marker': 'captcha',
                }
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'status_code': 200,
                'content_type': 'text/html',
                'title': 'Readable',
                'summary': 'Summary',
                'text': 'Evidence text',
                'evidence': [],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=2, read_top=1)

        self.assertTrue(result['ok'])
        self.assertTrue(result['failures'][0]['blocked'])
        self.assertEqual(result['failures'][0]['block_type'], 'captcha')
        self.assertEqual(result['failures'][0]['block_marker'], 'captcha')
        self.assertEqual(len(result['blocked_sources']), 1)
        self.assertIn('manual_handoff', result['blocked_sources'][0])
        self.assertEqual(result['manual_visit_links'][0]['url'], 'https://blocked.example/a')

    async def test_research_web_marks_repeated_block_skip_as_blocked_source(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'Blocked A', 'url': 'https://blocked.example/a', 'source': 'blocked.example', 'snippet': '', 'rank': 1},
                {'title': 'Blocked B', 'url': 'https://blocked.example/b', 'source': 'blocked.example', 'snippet': '', 'rank': 2},
                {'title': 'Readable', 'url': 'https://readable.example/c', 'source': 'readable.example', 'snippet': '', 'rank': 3},
            ],
        }

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            if 'blocked.example' in url:
                return {
                    'ok': False,
                    'url': url,
                    'message': 'Page appears blocked by captcha or anti-bot challenge: captcha',
                    'blocked': True,
                    'block_type': 'captcha',
                    'block_marker': 'captcha',
                }
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'status_code': 200,
                'content_type': 'text/html',
                'title': 'Readable',
                'summary': 'Summary',
                'text': 'Evidence text',
                'evidence': [],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=3, read_top=1)

        self.assertTrue(result['ok'])
        self.assertEqual(len(result['blocked_sources']), 2)
        self.assertEqual(len(result['manual_visit_links']), 2)
        self.assertTrue(result['failures'][1]['blocked'])
        self.assertEqual(result['failures'][1]['block_type'], 'blocked')
        self.assertIn('manual_handoff', result['failures'][1])

    async def test_research_web_uses_recovered_source_after_block(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'Blocked', 'url': 'https://example.com/article', 'source': 'example.com', 'snippet': '', 'rank': 1},
            ],
        }
        calls: list[str] = []

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            calls.append(url)
            if url == 'https://example.com/article':
                return {
                    'ok': False,
                    'url': url,
                    'message': 'Page appears blocked by captcha or anti-bot challenge: captcha',
                    'blocked': True,
                    'block_type': 'captcha',
                    'block_marker': 'captcha',
                }
            if url == 'https://example.com/article?output=1':
                return {
                    'ok': True,
                    'source_id': source_id,
                    'url': url,
                    'final_url': url,
                    'status_code': 200,
                    'content_type': 'text/html',
                    'title': 'Recovered',
                    'summary': 'Summary',
                    'text': 'Recovered evidence text',
                    'evidence': [],
                }
            return {'ok': False, 'url': url, 'message': 'not found'}

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=1, read_top=1)

        self.assertTrue(result['ok'])
        self.assertEqual(calls, ['https://example.com/article', 'https://example.com/article?output=1'])
        self.assertEqual(result['sources'][0]['url'], 'https://example.com/article?output=1')
        self.assertEqual(result['sources'][0]['recovered_from']['url'], 'https://example.com/article')
        self.assertTrue(result['failures'][0]['recovery_attempts'][0]['ok'])

    async def test_research_web_passes_render_to_reads(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'A', 'url': 'https://example.com/a', 'source': 'example.com', 'snippet': '', 'rank': 1},
            ],
        }
        render_values: list[bool] = []

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            render_values.append(render)
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'status_code': 200,
                'content_type': 'text/html; browser-rendered',
                'title': 'Rendered',
                'summary': 'Summary',
                'text': 'Rendered evidence text',
                'evidence': [],
                'rendered': render,
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=1, read_top=1, render=True)

        self.assertTrue(result['ok'])
        self.assertTrue(result['render'])
        self.assertEqual(render_values, [True])


if __name__ == '__main__':
    unittest.main()
