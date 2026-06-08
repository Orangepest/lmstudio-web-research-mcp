from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from web_research.cache import cache
from web_research.search import (
    _PROVIDER_BACKOFF_UNTIL,
    _normalize_similar_search_query,
    is_duckduckgo_challenge,
    normalize_url,
    parse_brave_results,
    parse_duckduckgo_lite_results,
    parse_duckduckgo_results,
    parse_mojeek_results,
    parse_searxng_json_results,
    parse_searxng_results,
    web_search,
)


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request('GET', 'https://search.example/')
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(f'HTTP {self.status_code}', request=request, response=response)


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
        _PROVIDER_BACKOFF_UNTIL.clear()

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

    def test_site_filter_does_not_match_host_substrings(self) -> None:
        html = '''
        <div class="result"><a class="result__a" href="https://notexample.com/a">Wrong</a></div>
        <div class="result"><a class="result__a" href="https://docs.example.com/b">Right</a></div>
        '''

        results = parse_duckduckgo_results(html, 10, site='example.com')

        self.assertEqual([item['url'] for item in results], ['https://docs.example.com/b'])

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

    def test_parse_brave_results_extracts_links(self) -> None:
        html = '''
        <div class="snippet" data-type="web">
          <a href="https://example.com/report">
            <div class="title search-snippet-title">Example Report</div>
          </a>
          <div class="content">Useful Brave snippet.</div>
        </div>
        <div class="snippet" data-type="web">
          <a href="https://other.test/report">
            <div class="title search-snippet-title">Other Report</div>
          </a>
        </div>
        '''

        results = parse_brave_results(html, 5, site='example.com')

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['url'], 'https://example.com/report')
        self.assertEqual(results[0]['source'], 'example.com')
        self.assertEqual(results[0]['title'], 'Example Report')
        self.assertEqual(results[0]['snippet'], 'Useful Brave snippet.')

    def test_parse_searxng_results_extracts_links(self) -> None:
        html = '''
        <article class="result">
          <h3><a href="https://example.com/report">Example Report</a></h3>
          <p class="content">Useful SearXNG snippet.</p>
          <div class="engines"><span>google</span><span>bing</span></div>
        </article>
        '''

        results = parse_searxng_results(html, 5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['url'], 'https://example.com/report')
        self.assertEqual(results[0]['source'], 'example.com')
        self.assertEqual(results[0]['title'], 'Example Report')
        self.assertEqual(results[0]['snippet'], 'Useful SearXNG snippet.')
        self.assertEqual(results[0]['engines'], ['google', 'bing'])

    def test_parse_searxng_json_results_extracts_links(self) -> None:
        text = '''
        {
          "results": [
            {"title": "Bad", "url": "https://notexample.com/report", "content": "Nope"},
            {"title": "Example JSON", "url": "https://docs.example.com/report", "content": "JSON snippet.", "engines": ["brave"]}
          ]
        }
        '''

        results = parse_searxng_json_results(text, 5, site='example.com')

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['url'], 'https://docs.example.com/report')
        self.assertEqual(results[0]['title'], 'Example JSON')
        self.assertEqual(results[0]['snippet'], 'JSON snippet.')
        self.assertEqual(results[0]['engines'], ['brave'])

    def test_parse_duckduckgo_lite_results_extracts_lite_markup(self) -> None:
        html = '''
        <table>
          <tr><td><a class="result-link" href="/l/?uddg=https%3A%2F%2Fexample.com%2Flite">Lite Result</a></td></tr>
          <tr><td class="result-snippet">Lite snippet text.</td></tr>
        </table>
        '''

        results = parse_duckduckgo_lite_results(html, 5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['url'], 'https://example.com/lite')
        self.assertEqual(results[0]['title'], 'Lite Result')
        self.assertEqual(results[0]['snippet'], 'Lite snippet text.')

    def test_normalize_similar_search_query_strips_common_followup_suffixes(self) -> None:
        self.assertEqual(
            _normalize_similar_search_query('Machine Learning official source latest additional evidence'),
            'machine learning',
        )

    def test_web_search_returns_local_searxng_results_first(self) -> None:
        html = '''
        <article class="result">
          <h3><a href="https://live.example/news">Live Result</a></h3>
          <p class="content">Current live snippet.</p>
        </article>
        '''
        client = FakeSearchClient([FakeResponse(html)])

        with patch('web_research.search.httpx.Client', return_value=client):
            payload = web_search('current news', max_results=5)

        self.assertTrue(payload['ok'])
        self.assertEqual(payload['provider'], 'searxng_local_html')
        self.assertFalse(payload['cached'])
        self.assertEqual(payload['backend_attempts'][0]['provider'], 'searxng_local_html')
        self.assertTrue(payload['backend_attempts'][0]['ok'])
        self.assertEqual(payload['backend_attempts'][0]['result_count'], 1)
        self.assertEqual(payload['results'][0]['url'], 'https://live.example/news')
        self.assertNotEqual(payload['provider'], 'local_index')
        self.assertIn('127.0.0.1:8888', client.urls[0])
        self.assertNotIn('format=json', client.urls[0])
        self.assertIn('engines=google', client.urls[0])

    def test_web_search_returns_local_searxng_json_results_first(self) -> None:
        response = '{"results":[{"title":"JSON Result","url":"https://live.example/json","content":"JSON snippet."}]}'
        client = FakeSearchClient([FakeResponse(response)])

        with patch('web_research.search._configured_search_providers', return_value=['searxng_local']), patch('web_research.search.httpx.Client', return_value=client):
            payload = web_search('current news', max_results=5)

        self.assertTrue(payload['ok'])
        self.assertEqual(payload['provider'], 'searxng_local')
        self.assertEqual(payload['results'][0]['url'], 'https://live.example/json')
        self.assertEqual(len(client.urls), 1)

    def test_web_search_can_fall_back_to_local_searxng_html_when_json_forbidden(self) -> None:
        html = '''
        <article class="result">
          <h3><a href="https://live.example/html">HTML Result</a></h3>
          <p class="content">HTML fallback snippet.</p>
        </article>
        '''
        client = FakeSearchClient([FakeResponse('json disabled', status_code=403), FakeResponse(html)])

        with patch('web_research.search._configured_search_providers', return_value=['searxng_local', 'searxng_local_html']), patch('web_research.search.httpx.Client', return_value=client):
            payload = web_search('current news', max_results=5)

        self.assertTrue(payload['ok'])
        self.assertEqual(payload['provider'], 'searxng_local_html')
        self.assertEqual([item['provider'] for item in payload['backend_attempts']], ['searxng_local', 'searxng_local_html'])
        self.assertFalse(payload['backend_attempts'][0]['ok'])
        self.assertTrue(payload['backend_attempts'][1]['ok'])
        self.assertIn('/search?q=current+news&format=json', client.urls[0])
        self.assertIn('engines=google', client.urls[0])
        self.assertIn('/search?q=current+news', client.urls[1])
        self.assertNotIn('format=json', client.urls[1])
        self.assertIn('engines=google', client.urls[1])
        self.assertEqual(payload['results'][0]['url'], 'https://live.example/html')

    def test_web_search_rejects_blank_query_without_network(self) -> None:
        with patch('web_research.search.httpx.Client') as client_cls:
            payload = web_search('   ', max_results=5)

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['message'], 'web search query must not be blank')
        self.assertEqual(payload['backend_attempts'], [])
        client_cls.assert_not_called()

    def test_web_search_reuses_normalized_cache_for_suffix_variants(self) -> None:
        html = '''
        <article class="result">
          <h3><a href="https://live.example/base">Base Result</a></h3>
          <p class="content">Base snippet.</p>
        </article>
        '''
        client = FakeSearchClient([FakeResponse(html)])

        with patch('web_research.search.httpx.Client', return_value=client):
            first = web_search('machine learning', max_results=5)
            second = web_search('machine learning additional evidence', max_results=5)

        self.assertTrue(first['ok'])
        self.assertTrue(second['ok'])
        self.assertFalse(first['cached'])
        self.assertTrue(second['cached'])
        self.assertEqual(second['cache_match'], 'normalized_query')
        self.assertEqual(second['original_query'], 'machine learning additional evidence')
        self.assertEqual(second['results'][0]['url'], 'https://live.example/base')
        self.assertEqual(len(client.urls), 1)

    def test_web_search_falls_back_to_mojeek_after_empty_searxng(self) -> None:
        empty_html = '<html><body>No results here</body></html>'
        html = '''
        <li class="r1">
          <h2><a class="title" href="https://live.example/news">Live Result</a></h2>
          <p class="s">Current live snippet.</p>
        </li>
        '''
        client = FakeSearchClient([FakeResponse(empty_html), FakeResponse(html)])

        with patch('web_research.search._configured_search_providers', return_value=['searxng_local_html', 'mojeek_html']), patch('web_research.search.httpx.Client', return_value=client):
            payload = web_search('current news', max_results=5)

        self.assertTrue(payload['ok'])
        self.assertEqual(payload['provider'], 'mojeek_html')
        self.assertFalse(payload['cached'])
        self.assertEqual([item['provider'] for item in payload['backend_attempts']], ['searxng_local_html', 'mojeek_html'])
        self.assertFalse(payload['backend_attempts'][0]['ok'])
        self.assertTrue(payload['backend_attempts'][1]['ok'])
        self.assertEqual(payload['results'][0]['url'], 'https://live.example/news')
        self.assertNotEqual(payload['provider'], 'local_index')
        self.assertEqual(len(client.urls), 2)

    def test_web_search_falls_back_to_brave_before_duckduckgo(self) -> None:
        empty_html = '<html><body>No results here</body></html>'
        brave_html = '''
        <div class="snippet" data-type="web">
          <a href="https://live.example/brave"><div class="title">Brave Result</div></a>
          <div class="content">Brave live snippet.</div>
        </div>
        '''
        client = FakeSearchClient([
            FakeResponse(empty_html),
            FakeResponse(empty_html),
            FakeResponse(brave_html),
        ])

        with patch('web_research.search._configured_search_providers', return_value=['searxng_local_html', 'mojeek_html', 'brave_html']), patch('web_research.search.httpx.Client', return_value=client):
            payload = web_search('machine learning', max_results=5)

        self.assertTrue(payload['ok'])
        self.assertEqual(payload['provider'], 'brave_html')
        self.assertEqual(payload['results'][0]['url'], 'https://live.example/brave')
        self.assertEqual(
            [item['provider'] for item in payload['backend_attempts']],
            ['searxng_local_html', 'mojeek_html', 'brave_html'],
        )
        self.assertEqual(len(client.urls), 3)

    def test_web_search_skips_provider_temporarily_after_blocking_failure(self) -> None:
        html = '''
        <div class="snippet" data-type="web">
          <a href="https://live.example/brave"><div class="title">Brave Result</div></a>
        </div>
        '''
        first_client = FakeSearchClient([FakeResponse('blocked', status_code=429), FakeResponse(html)])
        second_client = FakeSearchClient([FakeResponse(html)])

        with (
            patch('web_research.search._configured_search_providers', return_value=['brave_html', 'duckduckgo_lite']),
            patch('web_research.search.httpx.Client', return_value=first_client),
        ):
            first = web_search('machine learning', max_results=5)
        cache._items.clear()
        with (
            patch('web_research.search._configured_search_providers', return_value=['brave_html', 'duckduckgo_lite']),
            patch('web_research.search.httpx.Client', return_value=second_client),
        ):
            second = web_search('different machine learning query', max_results=5)

        self.assertTrue(first['ok'])
        self.assertEqual([item['provider'] for item in first['backend_attempts']], ['brave_html', 'duckduckgo_lite'])
        self.assertTrue(second['ok'])
        self.assertEqual([item['provider'] for item in second['backend_attempts']], ['brave_html', 'duckduckgo_lite'])
        self.assertIn('backoff', second['backend_attempts'][0]['message'])
        self.assertFalse(second['backend_attempts'][0]['ok'])
        self.assertTrue(second['backend_attempts'][1]['ok'])
        self.assertEqual(len(second_client.urls), 1)

    def test_web_search_does_not_fall_back_to_local_index(self) -> None:
        empty_html = '<html><body>No results here</body></html>'
        client = FakeSearchClient([
            FakeResponse(empty_html),
            FakeResponse(empty_html),
            FakeResponse(empty_html),
            FakeResponse(empty_html),
        ])

        with patch('web_research.search.httpx.Client', return_value=client):
            payload = web_search('machine learning', max_results=5)

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['provider'], 'duckduckgo_lite')
        self.assertEqual(payload['results'], [])
        self.assertEqual(
            [item['provider'] for item in payload['backend_attempts']],
            ['searxng_local_html', 'searxng_local', 'brave_html', 'duckduckgo_lite'],
        )
        self.assertTrue(all(not item['ok'] for item in payload['backend_attempts']))
        self.assertFalse(payload['cached'])
        self.assertNotEqual(payload['provider'], 'local_index')
        self.assertEqual(len(client.urls), 4)


if __name__ == '__main__':
    unittest.main()
