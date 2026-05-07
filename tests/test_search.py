from __future__ import annotations

import unittest
from unittest.mock import patch

from web_research.cache import cache
from web_research.search import is_duckduckgo_challenge, normalize_url, parse_duckduckgo_results, parse_mojeek_results, web_search


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class FakeSearchClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def __enter__(self) -> 'FakeSearchClient':
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
        self.urls.append(url)
        return self.responses.pop(0)


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        cache._items.clear()

    def test_normalize_url_removes_fragment_and_trailing_slash(self) -> None:
        self.assertEqual(normalize_url('https://example.com/docs/#intro'), 'https://example.com/docs')

    def test_parse_duckduckgo_results_extracts_and_dedupes(self) -> None:
        html = '''
        <div class="result">
          <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs">Example Docs</a>
          <a class="result__snippet">A focused documentation result.</a>
          <span class="result__url">example.com/docs</span>
        </div>
        <div class="result">
          <a class="result__a" href="https://example.com/docs">Duplicate</a>
        </div>
        '''

        results = parse_duckduckgo_results(html, 5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Example Docs')
        self.assertEqual(results[0]['url'], 'https://example.com/docs')
        self.assertEqual(results[0]['source'], 'example.com/docs')
        self.assertEqual(results[0]['rank'], 1)

    def test_parse_duckduckgo_results_applies_site_filter(self) -> None:
        html = '''
        <div class="result"><a class="result__a" href="https://example.com/a">A</a></div>
        <div class="result"><a class="result__a" href="https://other.test/b">B</a></div>
        '''

        results = parse_duckduckgo_results(html, 10, site='example.com')

        self.assertEqual([item['url'] for item in results], ['https://example.com/a'])

    def test_duckduckgo_challenge_detection(self) -> None:
        html = '<form id="challenge-form" action="//duckduckgo.com/anomaly.js"></form>'

        self.assertTrue(is_duckduckgo_challenge(html))

    def test_parse_mojeek_results_extracts_links(self) -> None:
        html = '''
        <li class="r1">
          <a class="ob" href="https://example.com/news">https://example.com › news</a>
          <h2><a class="title" href="https://example.com/news">Example News Title</a></h2>
          <p class="s">Useful result snippet.</p>
        </li>
        '''

        results = parse_mojeek_results(html, 5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['url'], 'https://example.com/news')
        self.assertEqual(results[0]['source'], 'example.com')
        self.assertEqual(results[0]['title'], 'Example News Title')
        self.assertEqual(results[0]['snippet'], 'Useful result snippet.')

    def test_web_search_returns_live_mojeek_results(self) -> None:
        html = '''
        <li class="r1">
          <h2><a class="title" href="https://live.example/news">Live Result</a></h2>
          <p class="s">Current live snippet.</p>
        </li>
        '''
        client = FakeSearchClient([FakeResponse(html)])

        with patch('web_research.search.httpx.Client', return_value=client):
            payload = web_search('current news', max_results=5)

        self.assertTrue(payload['ok'])
        self.assertEqual(payload['provider'], 'mojeek_html')
        self.assertFalse(payload['cached'])
        self.assertEqual(payload['results'][0]['url'], 'https://live.example/news')
        self.assertNotEqual(payload['provider'], 'local_index')

    def test_web_search_does_not_fall_back_to_local_index(self) -> None:
        empty_html = '<html><body>No results here</body></html>'
        client = FakeSearchClient([
            FakeResponse(empty_html),
            FakeResponse(empty_html),
            FakeResponse(empty_html),
        ])

        with patch('web_research.search.httpx.Client', return_value=client):
            payload = web_search('machine learning', max_results=5)

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['provider'], 'duckduckgo_lite')
        self.assertEqual(payload['results'], [])
        self.assertFalse(payload['cached'])
        self.assertNotEqual(payload['provider'], 'local_index')
        self.assertEqual(len(client.urls), 3)


if __name__ == '__main__':
    unittest.main()
