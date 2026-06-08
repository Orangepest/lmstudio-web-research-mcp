from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from web_research.compact import compact_read_payload, compact_research_payload


class CompactPayloadTests(unittest.TestCase):
    def test_compact_research_payload_preserves_artifacts_and_trims_bulk(self) -> None:
        settings = SimpleNamespace(mcp_compact_results=True, mcp_result_excerpt_chars=80, mcp_result_max_items=1)
        payload = {
            'ok': True,
            'question': 'large research',
            'run_id': 'run-1',
            'run_path': '/tmp/run.json',
            'final_report_path': '/tmp/report.md',
            'final_report': 'A' * 200,
            'strategy': {
                'mode': 'deep',
                'query_plan': [{'query': 'one', 'extra': 'hidden'}, {'query': 'two'}],
            },
            'agent_loop': {
                'stop_reason': 'max_rounds',
                'planned_queries': [{'query': 'one', 'extra': 'hidden'}, {'query': 'two'}],
                'completed_queries': [{'query': 'one'}],
                'remaining_queries': [{'query': 'two'}],
                'decisions': [{'decision': 'searched_query'}, {'decision': 'reviewed'}],
                'rounds': [{'round': 1}, {'round': 2}],
            },
            'sources': [
                {
                    'source_id': 1,
                    'title': 'One',
                    'url': 'https://example.com/1',
                    'text': 'hidden',
                    'reliability': {'source_type': 'web', 'reliability_weight': 'supporting', 'extra': 'hidden'},
                    'document_metadata': {'document_type': 'pdf', 'page_count': 3},
                },
                {'source_id': 2, 'title': 'Two', 'url': 'https://example.com/2'},
            ],
            'evidence': [
                {'source_id': 1, 'citation': 'source:1[0:10]', 'quote': 'B' * 200},
                {'source_id': 2, 'citation': 'source:2[0:10]', 'quote': 'C'},
            ],
            'claims': [
                {'claim': 'one', 'supporting_sources': [1, 2], 'extra': 'hidden'},
                {'claim': 'two'},
            ],
            'blocked_sources': [
                {
                    'url': 'https://blocked.example/a',
                    'title': 'Blocked',
                    'blocked': True,
                    'block_type': 'captcha',
                    'message': 'blocked',
                    'manual_handoff': {
                        'url': 'https://blocked.example/a',
                        'message': 'Open manually.',
                        'extra': 'hidden',
                    },
                },
                {'url': 'https://blocked.example/b', 'blocked': True},
            ],
            'manual_visit_links': [
                {'url': 'https://blocked.example/a', 'message': 'Open manually.'},
                {'url': 'https://blocked.example/b', 'message': 'Open manually.'},
            ],
            'citation_audit': {
                'ok': False,
                'issue_count': 2,
                'uncited_claim_ids': ['a', 'b'],
                'issues': [{'code': 'one'}, {'code': 'two'}],
                'extra': 'hidden',
            },
            'final_answer_review': {
                'ok': False,
                'issue_count': 2,
                'high_count': 1,
                'issues': [{'code': 'one'}, {'code': 'two'}],
                'contradiction_review': {'ok': False, 'unresolved_count': 1, 'details': ['hidden']},
            },
            'answer_readiness': {
                'ok': False,
                'label': 'needs_review',
                'score': 65,
                'blockers': ['one', 'two'],
                'warnings': ['warn'],
            },
            'source_policy_audit': {'ok': False, 'skipped_source_count': 1},
            'source_quality': {
                'label': 'mixed',
                'score': 70,
                'warnings': ['one', 'two'],
                'domain_counts': {'a.com': 2, 'b.com': 1},
                'extra': 'hidden',
            },
            'source_selection_telemetry': {
                'planned_read_count': 3,
                'selected_authority_source_count': 1,
                'per_query': [{'query': 'one'}, {'query': 'two'}],
            },
            'remediation_plan': {'gap_count': 2, 'actions': [{'query': 'official source'}, {'query': 'second'}]},
            'research_coverage': {
                'planned_intent_count': 1,
                'satisfied_intent_count': 1,
                'gaps': ['gap one', 'gap two'],
                'by_intent': [{'intent': 'one'}, {'intent': 'two'}],
            },
            'source_freshness': {'current_sensitive': True, 'content_freshness_evidence': True},
            'report_synthesis': {'used': False, 'message': 'disabled'},
        }

        with patch('web_research.compact.settings', settings):
            compact = compact_research_payload(payload)

        self.assertTrue(compact['compact_result'])
        self.assertEqual(compact['tool_status'], 'completed_with_source_warnings')
        self.assertIn('individual sources were blocked', compact['tool_status_message'])
        self.assertEqual(compact['counts']['sources'], 2)
        self.assertEqual(compact['run_path'], '/tmp/run.json')
        self.assertEqual(compact['strategy']['query_plan_count'], 2)
        self.assertNotIn('extra', compact['strategy']['query_plan'][0])
        self.assertEqual(len(compact['sources']), 1)
        self.assertNotIn('text', compact['sources'][0])
        self.assertEqual(compact['sources'][0]['reliability']['source_type'], 'web')
        self.assertNotIn('extra', compact['sources'][0]['reliability'])
        self.assertEqual(compact['sources'][0]['document_metadata']['page_count'], 3)
        self.assertEqual(len(compact['evidence']), 1)
        self.assertEqual(compact['claims'][0]['supporting_sources_count'], 2)
        self.assertNotIn('extra', compact['claims'][0])
        self.assertIn('[truncated', compact['final_report_excerpt'])
        self.assertEqual(compact['agent_loop']['stop_reason'], 'max_rounds')
        self.assertEqual(compact['agent_loop']['planned_query_count'], 2)
        self.assertNotIn('extra', compact['agent_loop']['planned_queries'][0])
        self.assertEqual(compact['agent_loop']['decision_count'], 2)
        self.assertEqual(len(compact['agent_loop']['decisions']), 1)
        self.assertEqual(len(compact['blocked_sources']), 1)
        self.assertEqual(compact['blocked_sources'][0]['block_type'], 'captcha')
        self.assertNotIn('extra', compact['blocked_sources'][0]['manual_handoff'])
        self.assertEqual(len(compact['manual_visit_links']), 1)
        self.assertFalse(compact['citation_audit']['ok'])
        self.assertEqual(compact['citation_audit']['issue_count'], 2)
        self.assertEqual(compact['citation_audit']['issues_count'], 2)
        self.assertNotIn('extra', compact['citation_audit'])
        self.assertEqual(compact['final_answer_review']['issue_count'], 2)
        self.assertEqual(len(compact['final_answer_review']['issues']), 1)
        self.assertNotIn('details', compact['final_answer_review']['contradiction_review'])
        self.assertEqual(compact['answer_readiness']['label'], 'needs_review')
        self.assertEqual(compact['answer_readiness']['blockers_count'], 2)
        self.assertEqual(compact['answer_readiness']['blockers'], ['one'])
        self.assertEqual(compact['source_policy_audit']['skipped_source_count'], 1)
        self.assertEqual(compact['source_quality']['label'], 'mixed')
        self.assertEqual(compact['source_quality']['warnings_count'], 2)
        self.assertNotIn('extra', compact['source_quality'])
        self.assertEqual(compact['source_selection_telemetry']['planned_read_count'], 3)
        self.assertEqual(compact['source_selection_telemetry']['per_query_count'], 2)
        self.assertEqual(len(compact['source_selection_telemetry']['per_query']), 1)
        self.assertEqual(compact['remediation_plan']['gap_count'], 2)
        self.assertEqual(len(compact['remediation_plan']['actions']), 1)
        self.assertEqual(compact['research_coverage']['satisfied_intent_count'], 1)
        self.assertEqual(compact['research_coverage']['gaps_count'], 2)
        self.assertEqual(len(compact['research_coverage']['by_intent']), 1)
        self.assertTrue(compact['source_freshness']['content_freshness_evidence'])
        self.assertFalse(compact['report_synthesis']['used'])

    def test_compact_research_payload_marks_clean_success_completed(self) -> None:
        settings = SimpleNamespace(mcp_compact_results=True, mcp_result_excerpt_chars=80, mcp_result_max_items=1)

        with patch('web_research.compact.settings', settings):
            compact = compact_research_payload({'ok': True, 'sources': [], 'final_report': 'done'})

        self.assertEqual(compact['tool_status'], 'completed')
        self.assertEqual(compact['tool_status_message'], 'Tool call completed.')

    def test_compact_read_payload_replaces_full_text_with_excerpt(self) -> None:
        settings = SimpleNamespace(mcp_compact_results=True, mcp_result_excerpt_chars=80, mcp_result_max_items=1)

        with patch('web_research.compact.settings', settings):
            compact = compact_read_payload(
                {
                    'ok': True,
                    'text': 'A' * 200,
                    'title': 'Doc',
                    'links': [
                        {'url': 'https://example.com/a', 'text': 'A', 'domain': 'example.com', 'file_type': 'html'},
                        {'url': 'https://example.com/b', 'text': 'B', 'domain': 'example.com', 'file_type': 'pdf'},
                    ],
                }
            )

        self.assertTrue(compact['compact_result'])
        self.assertNotIn('text', compact)
        self.assertIn('text_excerpt', compact)
        self.assertGreater(compact['text_omitted_chars'], 0)
        self.assertEqual(compact['link_count'], 2)
        self.assertEqual(len(compact['links']), 1)
        self.assertEqual(compact['links_omitted_count'], 1)


if __name__ == '__main__':
    unittest.main()
