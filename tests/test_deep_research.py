from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mcp_server.server import (
    _build_gap_follow_up_plan,
    _parse_safe_continue_input,
    continue_research_run,
    deep_research,
    resume_deep_research,
    safe_continue_research_run,
    safe_resume_deep_research,
)
from web_research.cache import cache


class DeepResearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._items.clear()

    def test_gap_follow_up_plan_targets_missing_primary_and_single_source_claims(self) -> None:
        plan = _build_gap_follow_up_plan(
            'local research agents',
            {
                'sources': [
                    {
                        'source_id': 1,
                        'final_url': 'https://blog.example/post',
                        'reliability': {'source_type': 'blog', 'reliability_weight': 'supporting'},
                    }
                ],
                'claims': [
                    {'claim': 'A', 'supporting_sources': [1]},
                    {'claim': 'B', 'supporting_sources': [1]},
                ],
                'source_quality': {'unique_domain_count': 1},
                'citation_validation': {'ok': True},
                'research_quality': {'label': 'thin', 'gaps': []},
                'blocked_sources': [],
                'recent_changes': [],
            },
            set(),
            limit=4,
        )

        queries = [item.query for item in plan]

        self.assertTrue(any('official documentation primary source' in query for query in queries))
        self.assertTrue(any('corroborating evidence' in query for query in queries))

    def test_gap_follow_up_plan_targets_blocked_sources_and_conflicts(self) -> None:
        plan = _build_gap_follow_up_plan(
            'subscription cancellation rules',
            {
                'sources': [{'source_id': 1, 'final_url': 'https://ftc.gov', 'reliability': {'source_type': 'government'}}],
                'claims': [{'claim': 'A', 'supporting_sources': [1], 'conflicting_sources': [2]}],
                'source_quality': {'unique_domain_count': 2},
                'citation_validation': {'ok': True},
                'research_quality': {'label': 'moderate', 'gaps': []},
                'blocked_sources': [{'url': 'https://blocked.example'}],
                'recent_changes': [{'quote': 'updated'}],
            },
            set(),
            limit=4,
        )

        queries = [item.query for item in plan]

        self.assertTrue(any('conflicting evidence comparison' in query for query in queries))
        self.assertTrue(any('alternate source mirror official' in query for query in queries))
        self.assertTrue(any(item.intent == 'contradiction_resolution' for item in plan))

    def test_gap_follow_up_plan_targets_coverage_freshness_and_citation_audits(self) -> None:
        plan = _build_gap_follow_up_plan(
            'LM Studio latest MCP behavior',
            {
                'sources': [
                    {'source_id': 1, 'final_url': 'https://docs.example.com', 'reliability': {'source_type': 'documentation'}},
                    {'source_id': 2, 'final_url': 'https://news.example.com', 'reliability': {'source_type': 'news'}},
                ],
                'claims': [{'claim': 'A', 'supporting_sources': [1, 2]}],
                'source_quality': {'unique_domain_count': 1},
                'citation_validation': {'ok': True},
                'citation_audit': {'ok': False, 'issues': ['1 claim lacks supporting citations.']},
                'research_coverage': {'missing_intents': ['primary_source'], 'gaps': ['No strong primary source was selected.']},
                'source_freshness': {
                    'current_sensitive': True,
                    'content_freshness_evidence': False,
                    'gaps': ['No recent-change evidence snippets were extracted.'],
                },
                'research_quality': {'label': 'thin', 'gaps': []},
                'blocked_sources': [],
                'recent_changes': [],
            },
            set(),
            limit=8,
        )

        queries = [item.query for item in plan]

        self.assertTrue(any('primary source' in query for query in queries))
        self.assertTrue(any('cited supporting evidence' in query for query in queries))
        self.assertTrue(any('latest' in query and 'changelog' in query for query in queries))

    def test_gap_follow_up_plan_uses_final_answer_review_issues(self) -> None:
        plan = _build_gap_follow_up_plan(
            'local coding assistant privacy',
            {
                'sources': [
                    {'source_id': 1, 'final_url': 'https://docs.example.com', 'reliability': {'source_type': 'documentation'}},
                    {'source_id': 2, 'final_url': 'https://news.example.com', 'reliability': {'source_type': 'news'}},
                ],
                'claims': [{'claim': 'A', 'supporting_sources': [1, 2]}],
                'source_quality': {'unique_domain_count': 2, 'primary_source_count': 1},
                'citation_validation': {'ok': True},
                'citation_audit': {'ok': True},
                'research_coverage': {'missing_intents': [], 'gaps': []},
                'source_freshness': {'current_sensitive': False, 'gaps': []},
                'research_quality': {'label': 'moderate', 'gaps': []},
                'final_answer_review': {
                    'ok': False,
                    'issues': [
                        {
                            'code': 'single_source_claims',
                            'severity': 'medium',
                            'message': 'Claims need corroboration.',
                        }
                    ],
                    'contradiction_review': {
                        'ok': False,
                        'conflicted_claim_count': 1,
                        'follow_up_searches': ['local coding assistant privacy disputed telemetry claim official clarification'],
                        'retrieval_plan': [
                            {
                                'query': 'local coding assistant privacy disputed telemetry claim official evidence',
                                'intent': 'contradiction_resolution',
                                'rationale': 'Resolve disputed telemetry claim.',
                                'claim_id': 1,
                            }
                        ],
                    },
                },
                'blocked_sources': [],
                'recent_changes': [{'quote': 'recent'}],
                'recommended_next_searches': [],
            },
            set(),
            limit=4,
        )

        queries = [item.query for item in plan]

        self.assertTrue(any('corroborating evidence multiple sources' in query for query in queries))
        self.assertTrue(any('disputed telemetry claim official clarification' in query for query in queries))
        self.assertTrue(any(item.intent == 'contradiction_resolution' for item in plan))
        self.assertTrue(any('official evidence' in item.query for item in plan if item.intent == 'contradiction_resolution'))

    def test_gap_follow_up_plan_prioritizes_contradictions_before_generic_gaps(self) -> None:
        plan = _build_gap_follow_up_plan(
            'browser automation actions',
            {
                'sources': [{'source_id': 1}, {'source_id': 2}],
                'claims': [
                    {
                        'claim_id': 1,
                        'claim': 'Browser automation supports silent actions.',
                        'supporting_sources': [1],
                        'conflicting_sources': [2],
                    }
                ],
                'source_quality': {'unique_domain_count': 2},
                'research_quality': {'label': 'thin', 'gaps': ['Only two sources were selected.']},
                'final_answer_review': {
                    'ok': False,
                    'issues': [{'code': 'conflicted_claims', 'severity': 'high'}],
                    'contradiction_review': {'conflicted_claim_count': 1, 'retrieval_plan': []},
                },
                'blocked_sources': [],
                'recent_changes': [{'quote': 'recent'}],
                'recommended_next_searches': [],
            },
            set(),
            limit=1,
        )

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].intent, 'contradiction_resolution')
        self.assertIn('conflicting evidence comparison', plan[0].query)

    def test_gap_follow_up_plan_uses_remediation_actions_before_generic_gaps(self) -> None:
        plan = _build_gap_follow_up_plan(
            'dating app monetization ROI',
            {
                'sources': [{'source_id': 1}],
                'claims': [{'claim': 'Boosts are high ROI.', 'supporting_sources': [1]}],
                'source_quality': {'selected_source_count': 1, 'unique_domain_count': 1, 'primary_source_count': 0},
                'research_quality': {'label': 'thin', 'gaps': ['Only one source selected.']},
                'source_selection_telemetry': {
                    'planned_low_value_source_count': 3,
                    'planned_authority_source_count': 1,
                    'selected_authority_source_count': 0,
                },
                'recommended_next_searches': [],
                'blocked_sources': [],
                'recent_changes': [],
            },
            set(),
            limit=2,
        )

        self.assertIn('primary source', plan[0].query)
        self.assertIn(plan[0].intent, {'gap_follow_up', 'contradiction_resolution'})

    async def test_deep_research_remaps_source_ids_after_merging(self) -> None:
        async def fake_research_web(**kwargs: object) -> dict:
            self.assertFalse(kwargs.get('persist'))
            self.assertEqual(kwargs.get('report_format'), 'quick_answer')
            query = str(kwargs['query'])
            if query.endswith('official source'):
                url = 'https://example.com/two'
                quote = 'second source evidence confirms LM Studio supports local web research workflows.'
                telemetry = {
                    'planned_read_count': 3,
                    'attempted_read_count': 2,
                    'selected_source_count': 1,
                    'planned_authority_source_count': 2,
                    'selected_authority_source_count': 1,
                    'planned_low_value_source_count': 1,
                    'planned_policy_skip_count': 0,
                    'trace_policy_skip_count': 0,
                    'duplicate_skip_count': 1,
                    'read_failure_count': 0,
                    'cache_hit_source_count': 0,
                    'decision_counts': {'selected': 1, 'skipped_duplicate_url': 1},
                    'read_selection_reason_counts': {'strong_source_candidate': 2},
                    'top_source_score_reasons': [{'reason': 'market_authority_domain', 'count': 1}],
                }
            else:
                url = 'https://example.com/one'
                quote = 'first source evidence shows LM Studio supports local web research workflows.'
                telemetry = {
                    'planned_read_count': 2,
                    'attempted_read_count': 1,
                    'selected_source_count': 1,
                    'planned_authority_source_count': 1,
                    'selected_authority_source_count': 1,
                    'planned_low_value_source_count': 0,
                    'planned_policy_skip_count': 0,
                    'trace_policy_skip_count': 0,
                    'duplicate_skip_count': 0,
                    'read_failure_count': 0,
                    'cache_hit_source_count': 1,
                    'decision_counts': {'selected': 1},
                    'read_selection_reason_counts': {'intent_match:baseline': 1},
                    'top_source_score_reasons': [{'reason': 'primary_source_hint', 'count': 1}],
                }
            return {
                'ok': True,
                'search': {'provider': 'test', 'results': [{'url': url}]},
                'sources': [
                    {
                        'source_id': 1,
                        'url': url,
                        'final_url': url,
                        'title': url,
                    }
                ],
                'evidence': [
                    {
                        'source_id': 1,
                        'url': url,
                        'title': url,
                        'quote': quote,
                        'char_range': [0, len(quote)],
                        'citation': f'source:1[0:{len(quote)}]',
                        'rank': 1,
                    }
                ],
                'selection_trace': [
                    {
                        'url': url,
                        'decision': 'selected',
                        'source_id': 1,
                    }
                ],
                'failures': [],
                'blocked_sources': [],
                'source_selection_telemetry': telemetry,
            }

        with patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            return_value={
                'saved': True,
                'run_id': 'deep-run',
                'run_path': '/tmp/deep-run/run.json',
                'created_at': '2026-06-04T00:00:00Z',
            },
        ), patch(
            'mcp_server.server.update_research_run',
            return_value={'ok': True, 'run_id': 'deep-run', 'run_path': '/tmp/deep-run/run.json', 'updated_at': 'now'},
        ):
            result = await deep_research('topic', breadth=2, report_format='quick_answer', follow_up_rounds=0)

        self.assertTrue(result['ok'])
        self.assertEqual([source['source_id'] for source in result['sources']], [1, 2])
        self.assertEqual(
            [(item['url'], item['source_id']) for item in result['evidence']],
            [('https://example.com/one', 1), ('https://example.com/two', 2)],
        )
        self.assertEqual(result['evidence'][0]['citation'], 'source:1[0:76]')
        self.assertEqual(result['evidence'][1]['citation'], 'source:2[0:80]')
        self.assertEqual(len(result['claims']), 2)
        self.assertEqual(result['claims'][0]['supporting_sources'], [1])
        self.assertEqual(len(result['selection_trace']), 2)
        self.assertEqual(result['selection_trace'][0]['intent'], 'baseline')
        self.assertEqual(
            [(item['url'], item['source_id']) for item in result['selection_trace']],
            [('https://example.com/one', 1), ('https://example.com/two', 2)],
        )
        self.assertEqual(result['source_quality']['selected_source_count'], 2)
        self.assertEqual(result['source_quality']['unique_domain_count'], 1)
        self.assertEqual(result['searches'][0]['selection_trace_count'], 1)
        self.assertEqual(result['source_selection_telemetry']['query_count_with_telemetry'], 2)
        self.assertEqual(result['source_selection_telemetry']['planned_read_count'], 5)
        self.assertEqual(result['source_selection_telemetry']['selected_authority_source_count'], 2)
        self.assertEqual(result['source_selection_telemetry']['planned_low_value_source_count'], 1)
        self.assertEqual(result['source_selection_telemetry']['cache_hit_source_count'], 1)
        self.assertEqual(result['source_selection_telemetry']['decision_counts']['selected'], 2)
        self.assertEqual(result['source_selection_telemetry']['per_query'][0]['planned_read_count'], 2)
        self.assertEqual(result['strategy']['search_backend_summary']['provider_counts'], {'test': 2})
        self.assertEqual(result['run_id'], 'deep-run')
        self.assertTrue(result['persistence']['ok'])
        self.assertEqual(result['report_format'], 'quick_answer')
        self.assertIn(result['research_quality']['label'], {'thin', 'moderate', 'strong'})
        self.assertIn('## Key Claims', result['final_report'])
        self.assertIn('source_table', result['reports'])
        self.assertTrue(result['citation_validation']['ok'])
        self.assertEqual(result['agent_loop']['stop_reason'], 'max_rounds')
        self.assertEqual(len(result['agent_loop']['planned_queries']), 2)
        self.assertEqual(len(result['agent_loop']['completed_queries']), 2)
        self.assertTrue(any(item['decision'] == 'searched_query' for item in result['agent_loop']['decisions']))

    async def test_deep_research_passes_site_constraints_from_plan(self) -> None:
        calls: list[dict] = []

        async def fake_research_web(**kwargs: object) -> dict:
            self.assertFalse(kwargs.get('persist'))
            calls.append(dict(kwargs))
            return {
                'ok': False,
                'search': {'provider': 'test', 'results': []},
                'sources': [],
                'evidence': [],
                'failures': [],
                'blocked_sources': [],
            }

        with patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            return_value={
                'saved': True,
                'run_id': 'deep-run',
                'run_path': '/tmp/deep-run/run.json',
                'created_at': '2026-06-04T00:00:00Z',
            },
        ), patch(
            'mcp_server.server.update_research_run',
            return_value={'ok': True, 'run_id': 'deep-run', 'run_path': '/tmp/deep-run/run.json', 'updated_at': 'now'},
        ):
            result = await deep_research('FTC subscription cancellation rule', breadth=4)

        self.assertEqual(result['strategy']['topic_profile']['kind'], 'regulatory')
        self.assertTrue(any(call.get('site') == '.gov' for call in calls))
        initial_calls = calls[: len(result['strategy']['query_plan'])]
        self.assertEqual([call.get('source_intent') for call in initial_calls], [item['intent'] for item in result['strategy']['query_plan']])
        self.assertTrue(all(call.get('source_intent') == 'gap_follow_up' for call in calls[len(initial_calls) :]))
        self.assertTrue(any(item.get('site') == '.gov' for item in result['strategy']['query_plan']))
        self.assertEqual(result['agent_loop']['stop_reason'], 'no_new_sources')
        self.assertEqual(len(result['agent_loop']['remaining_queries']), 0)

    async def test_deep_research_updates_checkpoint_and_completes_same_run(self) -> None:
        initial_statuses: list[str | None] = []
        updates: list[dict] = []

        async def fake_research_web(**kwargs: object) -> dict:
            url = f"https://example.com/{len(updates)}"
            quote = 'Checkpoint evidence shows LM Studio can resume long research work.'
            return {
                'ok': True,
                'search': {'provider': 'test', 'results': [{'url': url}]},
                'sources': [{'source_id': 1, 'url': url, 'final_url': url, 'title': url}],
                'evidence': [
                    {
                        'source_id': 1,
                        'url': url,
                        'title': url,
                        'quote': quote,
                        'char_range': [0, len(quote)],
                        'citation': f'source:1[0:{len(quote)}]',
                        'rank': 1,
                    }
                ],
                'selection_trace': [{'url': url, 'decision': 'selected', 'source_id': 1}],
                'failures': [],
                'blocked_sources': [],
            }

        def fake_update(run_id: str, payload: dict, *, status: str) -> dict:
            updates.append({'run_id': run_id, 'payload': payload, 'status': status})
            return {'ok': True, 'run_id': run_id, 'run_path': '/tmp/checkpoint/run.json', 'updated_at': 'now'}

        def fake_save(*_args: object, **kwargs: object) -> dict:
            initial_statuses.append(kwargs.get('status'))
            return {
                'saved': True,
                'run_id': 'checkpoint-run',
                'run_path': '/tmp/checkpoint/run.json',
                'created_at': '2026-06-04T00:00:00Z',
            }

        with patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            side_effect=fake_save,
        ), patch('mcp_server.server.update_research_run', side_effect=fake_update):
            result = await deep_research('topic', breadth=2)

        self.assertEqual(initial_statuses, ['in_progress'])
        self.assertEqual(result['run_id'], 'checkpoint-run')
        self.assertTrue(any(item['status'] == 'in_progress' for item in updates))
        self.assertEqual(updates[-1]['status'], 'completed')
        self.assertIn('checkpoint', updates[0]['payload'])
        self.assertIn('agent_loop', updates[0]['payload'])
        self.assertNotIn('_source_url', updates[0]['payload']['evidence'][0])

    async def test_deep_research_soft_timeout_returns_checkpoint_before_report_synthesis(self) -> None:
        async def fake_research_web(**kwargs: object) -> dict:
            await asyncio.sleep(0.01)
            quote = 'Soft timeout evidence shows deep research can pause before LM Studio cancels the tool call.'
            return {
                'ok': True,
                'search': {'provider': 'test', 'results': [{'url': 'https://example.com/timeout'}]},
                'sources': [
                    {
                        'source_id': 1,
                        'url': 'https://example.com/timeout',
                        'final_url': 'https://example.com/timeout',
                        'title': 'Timeout',
                    }
                ],
                'evidence': [
                    {
                        'source_id': 1,
                        'url': 'https://example.com/timeout',
                        'title': 'Timeout',
                        'quote': quote,
                        'char_range': [0, len(quote)],
                        'citation': f'source:1[0:{len(quote)}]',
                        'rank': 1,
                    }
                ],
                'selection_trace': [{'url': 'https://example.com/timeout', 'decision': 'selected', 'source_id': 1}],
                'failures': [],
                'blocked_sources': [],
            }

        updates: list[dict] = []

        def fake_update(run_id: str, payload: dict, *, status: str) -> dict:
            updates.append({'run_id': run_id, 'payload': payload, 'status': status})
            return {'ok': True, 'run_id': run_id, 'run_path': '/tmp/soft-timeout/run.json', 'updated_at': 'now'}

        with patch(
            'mcp_server.server.settings',
            SimpleNamespace(deep_research_soft_timeout_seconds=0.001),
        ), patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            return_value={
                'saved': True,
                'run_id': 'soft-timeout-run',
                'run_path': '/tmp/soft-timeout/run.json',
                'created_at': '2026-06-04T00:00:00Z',
            },
        ), patch('mcp_server.server.update_research_run', side_effect=fake_update), patch(
            'mcp_server.server.finalize_report_payload',
        ) as finalize_mock:
            result = await deep_research('topic', breadth=2, follow_up_rounds=0)

        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'in_progress')
        self.assertEqual(result['run_id'], 'soft-timeout-run')
        self.assertIn('safe_resume_deep_research(run_id="soft-timeout-run")', result['resume_tool_call'])
        self.assertEqual(result['agent_loop']['stop_reason'], 'soft_timeout_after_initial_query')
        self.assertEqual(result['phase_diagnostics']['likely_timeout_phase'], 'soft_timeout_after_initial_query')
        self.assertTrue(any(item['phase'] == 'initial_search_fetch' for item in result['phase_diagnostics']['phases']))
        self.assertTrue(updates)
        self.assertEqual(updates[-1]['status'], 'in_progress')
        finalize_mock.assert_not_called()

    async def test_deep_research_can_run_gap_follow_up_round(self) -> None:
        calls: list[str] = []

        async def fake_research_web(**kwargs: object) -> dict:
            calls.append(str(kwargs['query']))
            index = len(calls)
            url = f'https://example{index}.com/source'
            quote = f'Follow up round evidence {index} shows autonomous research can add coverage.'
            return {
                'ok': True,
                'search': {'provider': 'test', 'results': [{'url': url}]},
                'sources': [{'source_id': 1, 'url': url, 'final_url': url, 'title': f'Source {index}'}],
                'evidence': [
                    {
                        'source_id': 1,
                        'url': url,
                        'title': f'Source {index}',
                        'quote': quote,
                        'char_range': [0, len(quote)],
                        'citation': f'source:1[0:{len(quote)}]',
                        'rank': 1,
                    }
                ],
                'selection_trace': [{'url': url, 'decision': 'selected', 'source_id': 1}],
                'failures': [],
                'blocked_sources': [],
            }

        with patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            return_value={
                'saved': True,
                'run_id': 'deep-run',
                'run_path': '/tmp/deep-run/run.json',
                'created_at': '2026-06-04T00:00:00Z',
            },
        ), patch(
            'mcp_server.server.update_research_run',
            return_value={'ok': True, 'run_id': 'deep-run', 'run_path': '/tmp/deep-run/run.json', 'updated_at': 'now'},
        ):
            result = await deep_research('topic', breadth=1, follow_up_rounds=1)

        self.assertEqual(len(calls), 2)
        self.assertIn('official documentation primary source', calls[1])
        self.assertEqual(result['strategy']['follow_up_rounds'], 1)
        self.assertEqual(result['strategy']['auto_follow_up_plan'][0]['intent'], 'gap_follow_up')
        self.assertIn('research_quality', result)
        self.assertEqual(len(result['sources']), 2)
        self.assertIn('reviewer', [agent['name'] for agent in result['agent_loop']['agents']])
        self.assertTrue(result['agent_loop']['rounds'])
        self.assertGreaterEqual(result['agent_loop']['rounds'][0]['reviewer_issue_count'], 1)
        self.assertTrue(any(item.get('agent') == 'reviewer' and item['decision'] == 'reviewed_provisional_answer' for item in result['agent_loop']['decisions']))
        self.assertTrue(any(item.get('agent') == 'planner' and item['decision'] == 'planned_follow_up_queries' for item in result['agent_loop']['decisions']))
        self.assertTrue(any(item.get('agent') == 'executor' and item['decision'] == 'searched_follow_up_query' for item in result['agent_loop']['decisions']))
        self.assertTrue(any(item['decision'] == 'planned_follow_up_queries' for item in result['agent_loop']['decisions']))

    async def test_deep_research_skips_gap_follow_up_when_quality_is_strong(self) -> None:
        calls: list[str] = []

        async def fake_research_web(**kwargs: object) -> dict:
            calls.append(str(kwargs['query']))
            sources = []
            evidence = []
            quote = 'Strong research evidence shows autonomous follow up can stop when coverage is sufficient.'
            for index in range(5):
                source_id = index + 1
                url = f'https://strong{len(calls)}-{source_id}.example/report'
                sources.append({'source_id': source_id, 'url': url, 'final_url': url, 'title': f'Source {source_id}'})
                evidence.append(
                    {
                        'source_id': source_id,
                        'url': url,
                        'title': f'Source {source_id}',
                        'quote': quote,
                        'char_range': [0, len(quote)],
                        'citation': f'source:{source_id}[0:{len(quote)}]',
                        'rank': 1,
                    }
                )
            return {
                'ok': True,
                'search': {'provider': 'test', 'results': [{'url': source['url']} for source in sources]},
                'sources': sources,
                'evidence': evidence,
                'selection_trace': [{'url': source['url'], 'decision': 'selected', 'source_id': source['source_id']} for source in sources],
                'failures': [],
                'blocked_sources': [],
            }

        with patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            return_value={
                'saved': True,
                'run_id': 'deep-run',
                'run_path': '/tmp/deep-run/run.json',
                'final_report_path': '/tmp/deep-run/report.md',
                'created_at': '2026-06-04T00:00:00Z',
            },
        ), patch(
            'mcp_server.server.update_research_run',
            return_value={'ok': True, 'run_id': 'deep-run', 'run_path': '/tmp/deep-run/run.json', 'updated_at': 'now'},
        ):
            result = await deep_research('topic', breadth=3, follow_up_rounds=2)

        self.assertEqual(len(calls), 3)
        self.assertEqual(result['strategy']['auto_follow_up_plan'], [])
        self.assertEqual(result['research_quality']['label'], 'strong')
        self.assertEqual(result['agent_loop']['stop_reason'], 'strong_enough')

    async def test_deep_research_stops_follow_up_when_no_new_sources_are_added(self) -> None:
        calls: list[str] = []

        async def fake_research_web(**kwargs: object) -> dict:
            calls.append(str(kwargs['query']))
            quote = 'Repeated source evidence shows follow up can avoid looping without new sources.'
            return {
                'ok': True,
                'search': {'provider': 'test', 'results': [{'url': 'https://same.example/source'}]},
                'sources': [{'source_id': 1, 'url': 'https://same.example/source', 'final_url': 'https://same.example/source', 'title': 'Same'}],
                'evidence': [
                    {
                        'source_id': 1,
                        'url': 'https://same.example/source',
                        'title': 'Same',
                        'quote': quote,
                        'char_range': [0, len(quote)],
                        'citation': f'source:1[0:{len(quote)}]',
                        'rank': 1,
                    }
                ],
                'selection_trace': [{'url': 'https://same.example/source', 'decision': 'selected', 'source_id': 1}],
                'failures': [],
                'blocked_sources': [],
            }

        with patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            return_value={
                'saved': True,
                'run_id': 'deep-run',
                'run_path': '/tmp/deep-run/run.json',
                'created_at': '2026-06-04T00:00:00Z',
            },
        ), patch(
            'mcp_server.server.update_research_run',
            return_value={'ok': True, 'run_id': 'deep-run', 'run_path': '/tmp/deep-run/run.json', 'updated_at': 'now'},
        ):
            result = await deep_research('topic', breadth=1, follow_up_rounds=2)

        self.assertEqual(len(calls), 2)
        self.assertEqual(result['agent_loop']['stop_reason'], 'no_new_sources')
        self.assertEqual(result['agent_loop']['rounds'][0]['new_source_count'], 0)
        self.assertTrue(
            any(
                item.get('decision') == 'searched_follow_up_query' and item.get('new_source_count') == 0
                for item in result['agent_loop']['decisions']
            )
        )

    async def test_deep_research_clamps_follow_up_rounds_and_breadth(self) -> None:
        calls: list[str] = []

        async def fake_research_web(**kwargs: object) -> dict:
            calls.append(str(kwargs['query']))
            return {
                'ok': False,
                'search': {'provider': 'test', 'results': []},
                'sources': [],
                'evidence': [],
                'selection_trace': [],
                'failures': [],
                'blocked_sources': [],
            }

        with patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            return_value={
                'saved': True,
                'run_id': 'deep-run',
                'run_path': '/tmp/deep-run/run.json',
                'created_at': '2026-06-04T00:00:00Z',
            },
        ), patch(
            'mcp_server.server.update_research_run',
            return_value={'ok': True, 'run_id': 'deep-run', 'run_path': '/tmp/deep-run/run.json', 'updated_at': 'now'},
        ):
            result = await deep_research('topic', breadth=99, follow_up_rounds=99)

        self.assertEqual(result['strategy']['breadth'], 6)
        self.assertEqual(result['strategy']['follow_up_rounds'], 3)
        self.assertLessEqual(len(calls), 12)

    async def test_resume_deep_research_continues_unfinished_queries(self) -> None:
        calls: list[str] = []
        resume_payload = {
            'question': 'topic',
            'strategy': {'breadth': 2, 'read_top_per_query': 1, 'freshness': None, 'render': False},
            'searches': [{'query': 'topic', 'intent': 'baseline'}],
            'sources': [],
            'evidence': [],
            'selection_trace': [],
            'failures': [],
            'blocked_sources': [],
        }

        async def fake_research_web(**kwargs: object) -> dict:
            calls.append(str(kwargs['query']))
            return {
                'ok': False,
                'search': {'provider': 'test', 'results': []},
                'sources': [],
                'evidence': [],
                'selection_trace': [],
                'failures': [],
                'blocked_sources': [],
            }

        with patch(
            'mcp_server.server.load_research_run',
            return_value={
                'ok': True,
                'run': {'run_id': 'checkpoint-run', 'kind': 'deep_research', 'status': 'in_progress'},
                'payload': resume_payload,
            },
        ), patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.update_research_run',
            return_value={'ok': True, 'run_id': 'checkpoint-run', 'run_path': '/tmp/checkpoint/run.json', 'updated_at': 'now'},
        ):
            result = await resume_deep_research('checkpoint-run')

        self.assertEqual(result['run_id'], 'checkpoint-run')
        self.assertTrue(calls)
        self.assertNotIn('topic', calls)

    async def test_resume_deep_research_uses_saved_query_plan(self) -> None:
        calls: list[dict] = []
        resume_payload = {
            'question': 'topic',
            'strategy': {
                'breadth': 2,
                'read_top_per_query': 1,
                'freshness': None,
                'render': False,
                'query_plan': [
                    {'query': 'old baseline', 'intent': 'baseline', 'rationale': 'Saved baseline.'},
                    {'query': 'old official', 'intent': 'primary_source', 'rationale': 'Saved official.', 'site': '.gov'},
                ],
            },
            'searches': [{'query': 'old baseline', 'intent': 'baseline'}],
            'sources': [],
            'evidence': [],
            'selection_trace': [],
            'failures': [],
            'blocked_sources': [],
        }

        async def fake_research_web(**kwargs: object) -> dict:
            calls.append(dict(kwargs))
            return {
                'ok': False,
                'search': {'provider': 'test', 'results': []},
                'sources': [],
                'evidence': [],
                'selection_trace': [],
                'failures': [],
                'blocked_sources': [],
            }

        with patch(
            'mcp_server.server.load_research_run',
            return_value={
                'ok': True,
                'run': {'run_id': 'checkpoint-run', 'kind': 'deep_research', 'status': 'in_progress'},
                'payload': resume_payload,
            },
        ), patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.update_research_run',
            return_value={'ok': True, 'run_id': 'checkpoint-run', 'run_path': '/tmp/checkpoint/run.json', 'updated_at': 'now'},
        ):
            result = await resume_deep_research('checkpoint-run')

        self.assertEqual(result['run_id'], 'checkpoint-run')
        self.assertEqual([call['query'] for call in calls], ['old official'])
        self.assertEqual(calls[0]['site'], '.gov')

    async def test_resume_deep_research_preserves_report_format_and_auto_follow_up_plan(self) -> None:
        calls: list[dict] = []
        quote = 'Resume evidence shows saved strategy settings are preserved.'
        resume_payload = {
            'question': 'topic',
            'strategy': {
                'breadth': 2,
                'read_top_per_query': 1,
                'freshness': None,
                'render': False,
                'report_format': 'executive_brief',
                'follow_up_rounds': 0,
                'query_plan': [
                    {'query': 'old baseline', 'intent': 'baseline', 'rationale': 'Saved baseline.'},
                    {'query': 'old official', 'intent': 'primary_source', 'rationale': 'Saved official.'},
                ],
                'auto_follow_up_plan': [
                    {'query': 'old gap', 'intent': 'gap_follow_up', 'rationale': 'Saved gap.'},
                ],
            },
            'searches': [{'query': 'old baseline', 'intent': 'baseline'}],
            'sources': [],
            'evidence': [],
            'selection_trace': [],
            'failures': [],
            'blocked_sources': [],
        }

        async def fake_research_web(**kwargs: object) -> dict:
            calls.append(dict(kwargs))
            return {
                'ok': True,
                'search': {'provider': 'test', 'results': [{'url': 'https://example.com/resume'}]},
                'sources': [
                    {
                        'source_id': 1,
                        'url': 'https://example.com/resume',
                        'final_url': 'https://example.com/resume',
                        'title': 'Resume',
                    }
                ],
                'evidence': [
                    {
                        'source_id': 1,
                        'url': 'https://example.com/resume',
                        'title': 'Resume',
                        'quote': quote,
                        'char_range': [0, len(quote)],
                        'citation': f'source:1[0:{len(quote)}]',
                        'rank': 1,
                    }
                ],
                'selection_trace': [{'url': 'https://example.com/resume', 'decision': 'selected', 'source_id': 1}],
                'failures': [],
                'blocked_sources': [],
            }

        with patch(
            'mcp_server.server.load_research_run',
            return_value={
                'ok': True,
                'run': {'run_id': 'checkpoint-run', 'kind': 'deep_research', 'status': 'in_progress'},
                'payload': resume_payload,
            },
        ), patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.update_research_run',
            return_value={
                'ok': True,
                'run_id': 'checkpoint-run',
                'run_path': '/tmp/checkpoint/run.json',
                'final_report_path': '/tmp/checkpoint/report.md',
                'updated_at': 'now',
            },
        ):
            result = await resume_deep_research('checkpoint-run')

        self.assertEqual(calls[0]['report_format'], 'executive_brief')
        self.assertEqual(result['report_format'], 'executive_brief')
        self.assertTrue(result['final_report'].startswith('# Executive Brief'))
        self.assertEqual(result['strategy']['auto_follow_up_plan'][0]['query'], 'old gap')
        self.assertEqual(result['final_report_path'], '/tmp/checkpoint/report.md')

    async def test_resume_deep_research_returns_completed_payload(self) -> None:
        with patch(
            'mcp_server.server.load_research_run',
            return_value={
                'ok': True,
                'run': {'run_id': 'done', 'kind': 'deep_research', 'status': 'completed'},
                'payload': {'ok': True, 'question': 'done'},
            },
        ):
            result = await resume_deep_research('done')

        self.assertTrue(result['ok'])
        self.assertEqual(result['message'], 'Research run is already completed.')

    async def test_safe_resume_deep_research_delegates_to_resume_tool(self) -> None:
        with patch(
            'mcp_server.server.resume_deep_research',
            return_value={'ok': True, 'run_id': 'checkpoint-run'},
        ) as resume_mock:
            result = await safe_resume_deep_research('checkpoint-run')

        self.assertEqual(result, {'ok': True, 'run_id': 'checkpoint-run'})
        resume_mock.assert_awaited_once_with('checkpoint-run')

    async def test_continue_research_run_merges_follow_up_and_saves_child(self) -> None:
        parent_payload = {
            'ok': True,
            'question': 'parent question',
            'sources': [
                {
                    'source_id': 1,
                    'url': 'https://example.com/parent',
                    'final_url': 'https://example.com/parent',
                    'title': 'Parent',
                }
            ],
            'evidence': [
                {
                    'source_id': 1,
                    'url': 'https://example.com/parent',
                    'title': 'Parent',
                    'quote': 'Parent evidence shows LM Studio can keep prior research context.',
                    'char_range': [0, 64],
                    'citation': 'source:1[0:64]',
                    'rank': 1,
                }
            ],
            'selection_trace': [{'url': 'https://example.com/parent', 'decision': 'selected', 'source_id': 1}],
            'failures': [],
            'blocked_sources': [],
        }

        async def fake_research_web(**kwargs: object) -> dict:
            self.assertFalse(kwargs.get('persist'))
            self.assertEqual(kwargs.get('report_format'), 'source_table')
            return {
                'ok': True,
                'search': {'provider': 'test', 'results': [{'url': 'https://example.com/follow'}]},
                'sources': [
                    {
                        'source_id': 1,
                        'url': 'https://example.com/follow',
                        'final_url': 'https://example.com/follow',
                        'title': 'Follow',
                    }
                ],
                'evidence': [
                    {
                        'source_id': 1,
                        'url': 'https://example.com/follow',
                        'title': 'Follow',
                        'quote': 'Follow up evidence shows LM Studio can add new research context.',
                        'char_range': [0, 65],
                        'citation': 'source:1[0:65]',
                        'rank': 1,
                    }
                ],
                'selection_trace': [{'url': 'https://example.com/follow', 'decision': 'selected', 'source_id': 1}],
                'failures': [],
                'blocked_sources': [],
                'message': 'Research completed with sources',
            }

        with patch(
            'mcp_server.server.load_research_run',
            return_value={'ok': True, 'run': {'run_id': 'parent-run'}, 'payload': parent_payload},
        ), patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            return_value={
                'saved': True,
                'run_id': 'child-run',
                'run_path': '/tmp/child-run/run.json',
                'created_at': '2026-06-04T00:00:00Z',
            },
        ) as save_mock:
            result = await continue_research_run('parent-run', 'follow up topic', report_format='source_table')

        self.assertTrue(result['ok'])
        self.assertEqual(result['parent_run_id'], 'parent-run')
        self.assertEqual(result['run_id'], 'child-run')
        self.assertEqual([source['source_id'] for source in result['sources']], [1, 2])
        self.assertEqual(
            [(item['url'], item['source_id']) for item in result['evidence']],
            [('https://example.com/parent', 1), ('https://example.com/follow', 2)],
        )
        self.assertEqual(
            [(item['url'], item['source_id']) for item in result['selection_trace']],
            [('https://example.com/parent', 1), ('https://example.com/follow', 2)],
        )
        self.assertEqual(result['report_format'], 'source_table')
        self.assertIn('| ID | Title | URL |', result['final_report'])
        save_mock.assert_called_once()
        self.assertEqual(save_mock.call_args.kwargs['parent_run_id'], 'parent-run')

    async def test_continue_research_run_returns_load_failure(self) -> None:
        with patch(
            'mcp_server.server.load_research_run',
            return_value={'ok': False, 'run_id': 'missing', 'message': 'Research run not found: missing'},
        ):
            result = await continue_research_run('missing', 'follow up topic')

        self.assertFalse(result['ok'])
        self.assertEqual(result['message'], 'Research run not found: missing')

    async def test_continue_research_run_normalizes_invalid_report_format(self) -> None:
        parent_payload = {
            'ok': True,
            'question': 'parent question',
            'sources': [],
            'evidence': [],
            'selection_trace': [],
            'failures': [],
            'blocked_sources': [],
        }

        async def fake_research_web(**kwargs: object) -> dict:
            self.assertEqual(kwargs.get('report_format'), 'long_report')
            return {
                'ok': False,
                'search': {'provider': 'test', 'results': []},
                'sources': [],
                'evidence': [],
                'selection_trace': [],
                'failures': [],
                'blocked_sources': [],
                'message': 'No sources',
            }

        with patch(
            'mcp_server.server.load_research_run',
            return_value={'ok': True, 'run': {'run_id': 'parent-run'}, 'payload': parent_payload},
        ), patch('mcp_server.server.run_research_web', side_effect=fake_research_web), patch(
            'mcp_server.server.save_research_run',
            return_value={
                'saved': True,
                'run_id': 'child-run',
                'run_path': '/tmp/child-run/run.json',
                'final_report_path': '/tmp/child-run/report.md',
                'created_at': '2026-06-04T00:00:00Z',
            },
        ):
            result = await continue_research_run('parent-run', 'follow up topic', report_format='bad')

        self.assertEqual(result['report_format'], 'long_report')
        self.assertEqual(result['final_report'], result['reports']['long_report'])
        self.assertEqual(result['final_report_path'], '/tmp/child-run/report.md')

    def test_parse_safe_continue_input_uses_first_line_as_run_id(self) -> None:
        run_id, follow_up_query = _parse_safe_continue_input('run-123\nFind newer official sources\nand price changes')

        self.assertEqual(run_id, 'run-123')
        self.assertEqual(follow_up_query, 'Find newer official sources and price changes')

    def test_parse_safe_continue_input_rejects_missing_query(self) -> None:
        with self.assertRaises(ValueError):
            _parse_safe_continue_input('run-123')

    async def test_safe_continue_research_run_delegates_with_conservative_defaults(self) -> None:
        with patch(
            'mcp_server.server.continue_research_run',
            return_value={'ok': True, 'run_id': 'child-run'},
        ) as continue_mock:
            result = await safe_continue_research_run('parent-run\nFollow up on pricing')

        self.assertEqual(result, {'ok': True, 'run_id': 'child-run'})
        continue_mock.assert_awaited_once_with(
            run_id='parent-run',
            follow_up_query='Follow up on pricing',
            max_results=8,
            read_top=2,
            freshness=None,
            render=False,
            report_format='executive_brief',
        )

    async def test_safe_continue_research_run_returns_format_error(self) -> None:
        result = await safe_continue_research_run('parent-run')

        self.assertFalse(result['ok'])
        self.assertIn('expected_format', result)


if __name__ == '__main__':
    unittest.main()
