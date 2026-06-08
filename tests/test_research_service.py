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
                'text': 'Evidence text shows LM Studio supports retrieval citations for research.',
                'evidence': [
                    {
                        'source_id': source_id,
                        'url': url,
                        'title': 'A',
                        'quote': 'Evidence text shows LM Studio supports retrieval citations for research.',
                        'char_range': [0, 68],
                        'citation': f'source:{source_id}[0:68]',
                        'rank': 1,
                    }
                ],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=3, read_top=2, persist=False)

        self.assertTrue(result['ok'])
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(len(result['failures']), 1)
        self.assertEqual(result['citations'], ['source:1[0:68]'])
        self.assertEqual(result['selection_trace'][0]['decision'], 'selected')
        self.assertEqual(result['selection_trace'][1]['decision'], 'skipped_duplicate_url')
        self.assertEqual(result['selection_trace'][2]['decision'], 'read_failed')
        self.assertEqual(result['source_selection_telemetry']['planned_read_count'], 3)
        self.assertEqual(result['source_selection_telemetry']['attempted_read_count'], 3)
        self.assertEqual(result['source_selection_telemetry']['selected_source_count'], 1)
        self.assertEqual(result['source_selection_telemetry']['duplicate_skip_count'], 1)
        self.assertEqual(result['source_selection_telemetry']['read_failure_count'], 1)
        self.assertEqual(result['source_selection_telemetry']['repeated_domains'], {'example.com': 3})
        self.assertEqual(result['source_quality']['selected_source_count'], 1)
        self.assertEqual(result['source_quality']['unique_domain_count'], 1)
        self.assertEqual(result['source_quality']['domains'], ['example.com'])
        self.assertEqual(result['sources'][0]['reliability']['source_type'], 'web')
        self.assertIn('credibility', result['sources'][0]['reliability'])
        self.assertEqual(result['sources'][0]['reliability']['credibility']['domain'], 'example.com')
        self.assertEqual(result['sources'][0]['reliability']['credibility']['label'], 'supporting')
        self.assertIn('why_selected', result['sources'][0]['reliability'])
        self.assertIn('weaker_supporting_sources', result['source_quality'])
        self.assertEqual(result['source_quality']['credibility_label_counts'], {'supporting': 1})
        self.assertEqual(result['source_quality']['average_credibility_score'], 50.0)
        self.assertIn('downgrade_reasons', result['source_quality'])
        self.assertIn(
            'duplicate_heavy_results',
            [item['reason'] for item in result['source_quality']['downgrade_reasons']],
        )
        self.assertIn(
            'no_strong_primary_sources',
            [item['reason'] for item in result['source_quality']['downgrade_reasons']],
        )
        self.assertIn(
            'low_credibility_source_set',
            [item['reason'] for item in result['source_quality']['downgrade_reasons']],
        )
        self.assertEqual(result['research_coverage']['satisfied_intent_count'], 1)
        self.assertEqual(result['claims'][0]['claim'], 'Evidence text shows LM Studio supports retrieval citations for research.')
        self.assertEqual(result['claims'][0]['supporting_sources'], [1])
        self.assertIn('Some claims are supported by only one source.', result['uncertainties'])
        self.assertIn('## Key Claims', result['final_report'])
        self.assertFalse(result['report_synthesis']['enabled'])
        self.assertTrue(result['citation_validation']['ok'])
        self.assertIn('citation_audit', result)
        self.assertTrue(result['citation_audit']['claim_count'])
        self.assertIn('source_freshness', result)
        self.assertEqual(result['research_quality']['label'], 'thin')
        self.assertTrue(result['evidence_index']['ok'])
        self.assertGreaterEqual(result['evidence_index']['chunk_count'], 1)
        self.assertEqual(result['evidence_index']['top_chunks'][0]['source_id'], 1)
        self.assertIn('## Best Evidence', result['final_report'])
        self.assertTrue(result['claim_support']['ok'])
        self.assertEqual(result['claim_support']['supported_claim_count'], 1)
        self.assertIn('## Claim Support Table', result['final_report'])
        self.assertIn('executive_brief', result['reports'])
        self.assertTrue(result['recommended_next_searches'])

    async def test_research_web_records_intent_aware_read_selection(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test latest behavior',
            'results': [
                {'title': 'Docs', 'url': 'https://docs.example.com/a', 'source': 'docs.example.com', 'snippet': 'official documentation', 'rank': 1},
                {'title': 'Release notes', 'url': 'https://blog.example.com/release', 'source': 'blog.example.com', 'snippet': 'latest changelog updated 2026', 'rank': 2},
            ],
        }

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'title': url,
                'text': 'Latest evidence shows release notes describe current behavior.',
                'evidence': [
                    {
                        'source_id': source_id,
                        'url': url,
                        'title': url,
                        'quote': 'Latest evidence shows release notes describe current behavior.',
                        'char_range': [0, 62],
                        'citation': f'source:{source_id}[0:62]',
                        'rank': 1,
                    }
                ],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test latest behavior', max_results=2, read_top=1, persist=False, source_intent='freshness')

        self.assertEqual(result['source_intent'], 'freshness')
        self.assertEqual(result['planned_reads'][0]['url'], 'https://blog.example.com/release')
        self.assertEqual(result['planned_reads'][0]['read_selection_reason'], 'intent_match:freshness')
        self.assertEqual(result['source_selection_telemetry']['read_selection_reason_counts']['intent_match:freshness'], 1)
        self.assertEqual(result['selection_trace'][0]['source_intent'], 'freshness')
        self.assertIn('intent_freshness', result['selection_trace'][0]['source_intent_reasons'])

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
            result = await research_web('test', max_results=3, read_top=1, persist=False)

        self.assertTrue(result['ok'])
        self.assertEqual(calls, ['https://blocked.example/a', 'https://readable.example/c'])
        self.assertEqual(len(result['failures']), 1)
        self.assertEqual([item['decision'] for item in result['selection_trace']], ['read_failed', 'selected'])

    async def test_research_web_can_select_final_report_format(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [{'title': 'A', 'url': 'https://example.com/a', 'source': 'example.com', 'snippet': 'A', 'rank': 1}],
        }

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            quote = 'Evidence text shows selectable reports work for local research.'
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'status_code': 200,
                'content_type': 'text/html',
                'title': 'A',
                'summary': 'Summary',
                'text': quote,
                'evidence': [
                    {
                        'source_id': source_id,
                        'url': url,
                        'title': 'A',
                        'quote': quote,
                        'char_range': [0, len(quote)],
                        'citation': f'source:{source_id}[0:{len(quote)}]',
                        'rank': 1,
                    }
                ],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=1, read_top=1, report_format='executive_brief', persist=False)

        self.assertEqual(result['report_format'], 'executive_brief')
        self.assertTrue(result['final_report'].startswith('# Executive Brief: test'))
        self.assertIn('long_report', result['reports'])

    async def test_research_web_normalizes_invalid_report_format(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [{'title': 'A', 'url': 'https://example.com/a', 'source': 'example.com', 'snippet': 'A', 'rank': 1}],
        }

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            quote = 'Evidence text shows invalid report formats fall back safely.'
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'status_code': 200,
                'content_type': 'text/html',
                'title': 'A',
                'summary': 'Summary',
                'text': quote,
                'evidence': [
                    {
                        'source_id': source_id,
                        'url': url,
                        'title': 'A',
                        'quote': quote,
                        'char_range': [0, len(quote)],
                        'citation': f'source:{source_id}[0:{len(quote)}]',
                        'rank': 1,
                    }
                ],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=1, read_top=1, report_format='bad', persist=False)

        self.assertEqual(result['report_format'], 'long_report')
        self.assertEqual(result['final_report'], result['reports']['long_report'])

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
            result = await research_web('test', max_results=2, read_top=1, persist=False)

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
            result = await research_web('test', max_results=3, read_top=1, persist=False)

        self.assertTrue(result['ok'])
        self.assertEqual(len(result['blocked_sources']), 1)
        self.assertEqual(len(result['manual_visit_links']), 1)
        self.assertTrue(result['failures'][0]['blocked'])
        self.assertEqual(result['failures'][0]['block_type'], 'captcha')
        self.assertIn('manual_handoff', result['failures'][0])

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
            result = await research_web('test', max_results=1, read_top=1, persist=False)

        self.assertTrue(result['ok'])
        self.assertEqual(calls, ['https://example.com/article', 'https://example.com/article?output=1'])
        self.assertEqual(result['sources'][0]['url'], 'https://example.com/article?output=1')
        self.assertEqual(result['sources'][0]['recovered_from']['url'], 'https://example.com/article')
        self.assertTrue(result['failures'][0]['recovery_attempts'][0]['ok'])

    async def test_research_web_skips_source_policy_domains_without_fetching(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'ResearchGate', 'url': 'https://www.researchgate.net/publication/123', 'source': 'researchgate.net', 'snippet': '', 'rank': 1},
                {'title': 'Readable', 'url': 'https://readable.example/c', 'source': 'readable.example', 'snippet': '', 'rank': 2},
            ],
        }
        calls: list[str] = []

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            calls.append(url)
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
            result = await research_web('test', max_results=2, read_top=1, persist=False)

        self.assertTrue(result['ok'])
        self.assertEqual(calls, ['https://readable.example/c'])
        self.assertEqual(result['selection_trace'][0]['decision'], 'selected')
        self.assertEqual(result['selection_trace'][1]['decision'], 'skipped_source_policy')
        self.assertTrue(result['selection_trace'][1]['deferred'])
        self.assertEqual(result['selection_trace'][1]['skip_reason'], 'hostile_or_low_value_research_domain')

    async def test_research_web_skips_recovery_after_hard_block(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'Blocked', 'url': 'https://example.com/article', 'source': 'example.com', 'snippet': '', 'rank': 1},
                {'title': 'Readable', 'url': 'https://readable.example/c', 'source': 'readable.example', 'snippet': '', 'rank': 2},
            ],
        }
        calls: list[str] = []

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            calls.append(url)
            if url == 'https://example.com/article':
                return {
                    'ok': False,
                    'url': url,
                    'message': 'Page appears blocked by blocked or anti-bot challenge: HTTP 403',
                    'blocked': True,
                    'block_type': 'blocked',
                    'block_marker': 'HTTP 403',
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
            result = await research_web('test', max_results=2, read_top=1, persist=False)

        self.assertTrue(result['ok'])
        self.assertEqual(calls, ['https://example.com/article', 'https://readable.example/c'])
        self.assertTrue(result['failures'][0]['recovery_skipped'])
        self.assertEqual(result['failures'][0]['recovery_attempts'], [])
        self.assertEqual(result['selection_trace'][0]['recovery_skip_reason'], 'hard_block_or_no_recovery_domain')

    async def test_research_web_skips_duplicate_resolved_urls_and_reports_source_mix(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'Official docs', 'url': 'https://docs.example.com/page?utm_source=feed', 'source': 'docs.example.com', 'snippet': '', 'rank': 1},
                {'title': 'Official docs mirror', 'url': 'https://docs.example.com/page?ref=search', 'source': 'docs.example.com', 'snippet': '', 'rank': 2},
                {'title': 'Government report', 'url': 'https://agency.gov/report', 'source': 'agency.gov', 'snippet': '', 'rank': 3},
            ],
        }

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            final_url = 'https://docs.example.com/page' if 'docs.example.com' in url else url
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': final_url,
                'status_code': 200,
                'content_type': 'text/html',
                'title': 'Readable',
                'summary': 'Summary',
                'text': 'Evidence text',
                'evidence': [],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=3, read_top=3, persist=False)

        self.assertTrue(result['ok'])
        self.assertEqual(len(result['sources']), 2)
        self.assertIn('skipped_duplicate_url', [item['decision'] for item in result['selection_trace']])
        self.assertEqual(result['source_quality']['primary_source_count'], 2)
        self.assertEqual(result['source_quality']['source_type_counts']['documentation'], 1)
        self.assertEqual(result['source_quality']['source_type_counts']['government'], 1)
        self.assertEqual(result['source_quality']['credibility_label_counts'], {'high': 1, 'medium': 1})
        self.assertEqual(result['source_quality']['average_credibility_score'], 84.5)

    async def test_research_web_plans_reads_for_strong_source_mix(self) -> None:
        search_payload = {
            'ok': True,
            'query': 'test',
            'results': [
                {'title': 'Forum discussion', 'url': 'https://forum.example/a', 'source': 'forum.example', 'snippet': 'community comments', 'rank': 1},
                {'title': 'Blog guide', 'url': 'https://blog.example/b', 'source': 'blog.example', 'snippet': 'ultimate guide', 'rank': 2},
                {'title': 'Official docs', 'url': 'https://docs.example.com/api', 'source': 'docs.example.com', 'snippet': 'official documentation', 'rank': 3},
                {'title': 'Government report', 'url': 'https://agency.gov/report', 'source': 'agency.gov', 'snippet': 'official report', 'rank': 4},
            ],
        }
        calls: list[str] = []

        async def fake_read_url(url: str, query: str | None, render: bool, source_id: int) -> dict:
            calls.append(url)
            return {
                'ok': True,
                'source_id': source_id,
                'url': url,
                'final_url': url,
                'status_code': 200,
                'content_type': 'text/html',
                'title': url,
                'summary': 'Summary',
                'text': 'Official evidence text for source planning and source mix quality.',
                'evidence': [],
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=4, read_top=2, persist=False)

        self.assertEqual(set(calls), {'https://docs.example.com/api', 'https://agency.gov/report'})
        self.assertEqual([source['reliability']['reliability_weight'] for source in result['sources']], ['strong', 'strong'])
        self.assertEqual([item['read_selection_reason'] for item in result['selection_trace']], ['strong_source_candidate', 'strong_source_candidate'])
        self.assertEqual([item['read_selection_reason'] for item in result['planned_reads'][:2]], ['strong_source_candidate', 'strong_source_candidate'])
        self.assertEqual(result['source_quality']['primary_source_count'], 2)

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
            result = await research_web('test', max_results=1, read_top=1, render=True, persist=False)

        self.assertTrue(result['ok'])
        self.assertTrue(result['render'])
        self.assertEqual(render_values, [True])

    async def test_research_web_retries_empty_successful_fetch_with_rendering(self) -> None:
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
            if not render:
                return {
                    'ok': False,
                    'source_id': source_id,
                    'url': url,
                    'final_url': url,
                    'status_code': 200,
                    'content_type': 'text/html',
                    'title': 'Client App',
                    'summary': '',
                    'text': '',
                    'evidence': [],
                    'message': 'URL fetched',
                    'rendered': False,
                }
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
                'message': 'Rendered page fetched',
                'rendered': True,
            }

        with patch('web_research.service.web_search', return_value=search_payload), patch('web_research.service.read_url', side_effect=fake_read_url):
            result = await research_web('test', max_results=1, read_top=1, persist=False)

        self.assertTrue(result['ok'])
        self.assertEqual(render_values, [False, True])
        self.assertTrue(result['sources'][0]['rendered'])


if __name__ == '__main__':
    unittest.main()
