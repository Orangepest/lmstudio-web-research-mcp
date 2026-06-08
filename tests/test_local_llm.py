from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from web_research.local_llm import (
    review_claim_contradictions,
    synthesize_campaign_dossier,
    synthesize_research_report,
    validate_synthesized_campaign_dossier,
    validate_synthesized_report,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.posts: list[dict] = []

    async def __aenter__(self) -> 'FakeClient':
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, path: str, json: dict) -> FakeResponse:
        self.posts.append({'path': path, 'json': json})
        return FakeResponse(
            {
                'choices': [
                    {
                        'message': {
                            'content': '{"verdict":"contradiction","reason":"One claim says support exists and the other denies it."}'
                        }
                    }
                ]
            }
        )


class FakeSynthesisClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.posts: list[dict] = []

    async def __aenter__(self) -> 'FakeSynthesisClient':
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, path: str, json: dict) -> FakeResponse:
        self.posts.append({'path': path, 'json': json})
        return FakeResponse({'choices': [{'message': {'content': '# Polished Report\n\n- Uses source:1.\n'}}]})


class BadCitationSynthesisClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.posts: list[dict] = []

    async def __aenter__(self) -> 'BadCitationSynthesisClient':
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, path: str, json: dict) -> FakeResponse:
        self.posts.append({'path': path, 'json': json})
        return FakeResponse({'choices': [{'message': {'content': '# Bad Report\n\n- Uses source:9.\n'}}]})


class FailingSynthesisClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        return None

    async def __aenter__(self) -> 'FailingSynthesisClient':
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, path: str, json: dict) -> FakeResponse:
        raise ValueError('local model unavailable')


class LocalLlmTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_claim_contradictions_disabled_by_default(self) -> None:
        settings = SimpleNamespace(local_llm_contradiction_review=False)

        with patch('web_research.local_llm.settings', settings):
            result = await review_claim_contradictions([])

        self.assertFalse(result['enabled'])
        self.assertEqual(result['reviewed_pairs'], 0)

    async def test_review_claim_contradictions_marks_llm_conflict(self) -> None:
        settings = SimpleNamespace(
            local_llm_contradiction_review=True,
            local_llm_base_url='http://127.0.0.1:1234/v1',
            local_llm_timeout=1,
            local_llm_model='test-model',
        )
        claims = [
            {
                'claim_id': 1,
                'claim': 'LM Studio supports MCP servers through local configuration.',
                'supporting_sources': [1],
                'conflicting_sources': [],
                'confidence': 'low',
                'source_quality_notes': [],
            },
            {
                'claim_id': 2,
                'claim': 'LM Studio does not support MCP servers through local configuration.',
                'supporting_sources': [2],
                'conflicting_sources': [],
                'confidence': 'low',
                'source_quality_notes': [],
            },
        ]

        with patch('web_research.local_llm.settings', settings), patch('web_research.local_llm.httpx.AsyncClient', FakeClient):
            result = await review_claim_contradictions(claims)

        self.assertTrue(result['enabled'])
        self.assertEqual(result['reviewed_pairs'], 1)
        self.assertEqual(result['contradictions'], 1)
        self.assertEqual(claims[0]['conflicting_sources'], [2])
        self.assertEqual(claims[1]['conflicting_sources'], [1])
        self.assertEqual(claims[0]['conflict_reviews'][0]['method'], 'local_llm')
        self.assertIn('Local LLM judged this claim as conflicting with another claim.', claims[0]['source_quality_notes'])

    async def test_synthesize_research_report_disabled_by_default(self) -> None:
        settings = SimpleNamespace(local_llm_report_synthesis=False)

        with patch('web_research.local_llm.settings', settings):
            result = await synthesize_research_report({}, deterministic_report='# Fallback\n', report_format='long_report')

        self.assertFalse(result['enabled'])
        self.assertFalse(result['used'])

    async def test_synthesize_research_report_returns_polished_report(self) -> None:
        settings = SimpleNamespace(
            local_llm_report_synthesis=True,
            local_llm_base_url='http://127.0.0.1:1234/v1',
            local_llm_timeout=1,
            local_llm_model='test-model',
            local_llm_report_max_tokens=400,
        )
        payload = {
            'question': 'topic',
            'sources': [{'source_id': 1, 'title': 'Source', 'final_url': 'https://example.com'}],
            'claims': [{'claim': 'Claim one.', 'supporting_sources': [1]}],
            'evidence': [{'source_id': 1, 'citation': 'source:1[0:10]', 'quote': 'Claim one.'}],
        }

        with patch('web_research.local_llm.settings', settings), patch('web_research.local_llm.httpx.AsyncClient', FakeSynthesisClient):
            result = await synthesize_research_report(payload, deterministic_report='# Fallback\n', report_format='executive_brief')

        self.assertTrue(result['enabled'])
        self.assertTrue(result['used'])
        self.assertEqual(result['model'], 'test-model')
        self.assertIn('source:1', result['report'])
        self.assertTrue(result['validation']['ok'])

    def test_validate_synthesized_report_rejects_unknown_sources(self) -> None:
        validation = validate_synthesized_report(
            'This cites source:2.',
            {
                'sources': [{'source_id': 1}],
                'citation_validation': {'citation_count': 1},
                'research_quality': {'label': 'moderate'},
            },
        )

        self.assertFalse(validation['ok'])
        self.assertEqual(validation['unknown_source_ids'], [2])

    def test_validate_synthesized_report_requires_citations_and_uncertainty_when_needed(self) -> None:
        validation = validate_synthesized_report(
            'This is a polished answer with no caveats.',
            {
                'sources': [{'source_id': 1}],
                'citation_validation': {'citation_count': 1},
                'research_quality': {'label': 'thin'},
            },
        )

        self.assertFalse(validation['ok'])
        self.assertTrue(any('dropped all source ID citations' in issue for issue in validation['issues']))
        self.assertTrue(any('uncertainty' in issue for issue in validation['issues']))

    async def test_synthesize_research_report_rejects_invalid_rewrite(self) -> None:
        settings = SimpleNamespace(
            local_llm_report_synthesis=True,
            local_llm_base_url='http://127.0.0.1:1234/v1',
            local_llm_timeout=1,
            local_llm_model='test-model',
            local_llm_report_max_tokens=400,
        )
        payload = {
            'question': 'topic',
            'sources': [{'source_id': 1, 'title': 'Source', 'final_url': 'https://example.com'}],
            'claims': [{'claim': 'Claim one.', 'supporting_sources': [1]}],
            'evidence': [{'source_id': 1, 'citation': 'source:1[0:10]', 'quote': 'Claim one.'}],
            'citation_validation': {'citation_count': 1},
            'research_quality': {'label': 'moderate'},
        }

        with patch('web_research.local_llm.settings', settings), patch('web_research.local_llm.httpx.AsyncClient', BadCitationSynthesisClient):
            result = await synthesize_research_report(payload, deterministic_report='# Fallback\n', report_format='executive_brief')

        self.assertTrue(result['enabled'])
        self.assertFalse(result['used'])
        self.assertIn('rejected', result['message'])
        self.assertEqual(result['validation']['unknown_source_ids'], [9])

    async def test_synthesize_research_report_falls_back_on_failure(self) -> None:
        settings = SimpleNamespace(
            local_llm_report_synthesis=True,
            local_llm_base_url='http://127.0.0.1:1234/v1',
            local_llm_timeout=1,
            local_llm_model='test-model',
            local_llm_report_max_tokens=400,
        )

        with patch('web_research.local_llm.settings', settings), patch('web_research.local_llm.httpx.AsyncClient', FailingSynthesisClient):
            result = await synthesize_research_report({}, deterministic_report='# Fallback\n', report_format='long_report')

        self.assertTrue(result['enabled'])
        self.assertFalse(result['used'])
        self.assertIn('unavailable', result['message'])

    async def test_synthesize_campaign_dossier_returns_polished_dossier(self) -> None:
        settings = SimpleNamespace(
            local_llm_report_synthesis=False,
            local_llm_base_url='http://127.0.0.1:1234/v1',
            local_llm_timeout=1,
            local_llm_model='test-model',
            local_llm_report_max_tokens=400,
        )
        synthesis = {
            'manifest': {'campaign_id': 'campaign-1', 'objective': 'topic', 'counts': {'completed_runs': 1}},
            'sources': [{'campaign_source_id': 1, 'title': 'Source', 'url': 'https://example.com'}],
            'claims': [{'claim': 'Claim one.', 'supporting_campaign_sources': '1'}],
            'audit': {'runs': [{'run_id': 'run-1', 'query': 'topic'}]},
            'claim_count': 1,
            'missing_runs': [],
        }

        with patch('web_research.local_llm.settings', settings), patch('web_research.local_llm.httpx.AsyncClient', FakeSynthesisClient):
            result = await synthesize_campaign_dossier(synthesis, deterministic_dossier='# Fallback\n', enabled=True)

        self.assertTrue(result['enabled'])
        self.assertTrue(result['used'])
        self.assertEqual(result['model'], 'test-model')
        self.assertIn('source:1', result['dossier'])
        self.assertTrue(result['validation']['ok'])

    def test_validate_synthesized_campaign_dossier_rejects_unknown_sources(self) -> None:
        validation = validate_synthesized_campaign_dossier(
            'This cites source:9.',
            {'sources': [{'campaign_source_id': 1}], 'claim_count': 1},
        )

        self.assertFalse(validation['ok'])
        self.assertEqual(validation['unknown_source_ids'], [9])


if __name__ == '__main__':
    unittest.main()
