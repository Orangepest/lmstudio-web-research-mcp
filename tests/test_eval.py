from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from web_research.eval import build_eval_record, human_review_template, load_eval_tasks, score_research_payload


class EvalHarnessTests(unittest.TestCase):
    def test_load_eval_tasks_normalizes_task_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tasks.json'
            path.write_text(
                json.dumps(
                    {
                        'tasks': [
                            {
                                'id': 'task-one',
                                'category': 'technical',
                                'question': 'What is the best source?',
                                'expected_domains': ['Example.com'],
                                'required_checks': ['claim_support_present'],
                                'tags': ['smoke'],
                            }
                        ]
                    }
                ),
                encoding='utf-8',
            )

            tasks = load_eval_tasks(path)

        self.assertEqual(tasks[0]['id'], 'task-one')
        self.assertEqual(tasks[0]['tool'], 'research_web')
        self.assertEqual(tasks[0]['expected_domains'], ['example.com'])
        self.assertEqual(tasks[0]['required_checks'], ['claim_support_present'])
        self.assertEqual(tasks[0]['tags'], ['smoke'])

    def test_load_eval_tasks_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tasks.json'
            path.write_text(
                json.dumps({'tasks': [{'id': 'same', 'question': 'A'}, {'id': 'same', 'question': 'B'}]}),
                encoding='utf-8',
            )

            with self.assertRaises(ValueError):
                load_eval_tasks(path)

    def test_score_research_payload_rewards_traceable_source_coverage(self) -> None:
        payload = {
            'ok': True,
            'sources': [
                {'source_id': 1, 'final_url': 'https://docs.example.com/a'},
                {'source_id': 2, 'final_url': 'https://news.example.org/b'},
                {'source_id': 3, 'final_url': 'https://example.net/c'},
            ],
            'citations': ['source:1[0:10]', 'source:2[0:10]', 'source:3[0:10]'],
            'citation_validation': {'ok': True, 'citation_count': 3, 'invalid_citations': []},
            'research_quality': {'label': 'strong', 'score': 82},
            'recommended_next_searches': ['topic official source'],
            'final_report': 'The answer cites source:1 and source:2.',
        }

        score = score_research_payload(payload, {'expected_domains': ['example.com']})

        self.assertEqual(score['label'], 'pass')
        self.assertGreaterEqual(score['score'], 90)
        self.assertTrue(score['checks']['matches_expected_domains'])

    def test_score_research_payload_treats_raw_github_as_github(self) -> None:
        score = score_research_payload(
            {
                'ok': True,
                'sources': [{'source_id': 1, 'final_url': 'https://raw.githubusercontent.com/org/repo/HEAD/README.md'}],
                'citation_validation': {'ok': True, 'citation_count': 1, 'invalid_citations': []},
                'research_quality': {'label': 'thin', 'score': 40},
                'recommended_next_searches': ['topic official source'],
                'final_report': 'Uses source:1.',
            },
            {'expected_domains': ['github.com']},
        )

        self.assertTrue(score['checks']['matches_expected_domains'])

    def test_score_research_payload_flags_weak_uncited_output(self) -> None:
        score = score_research_payload(
            {
                'ok': False,
                'sources': [],
                'citation_validation': {'ok': False, 'citation_count': 0, 'invalid_citations': []},
                'research_quality': {'label': 'weak', 'score': 10},
                'final_report': 'No citations here.',
            },
            {'expected_domains': ['official.example']},
        )

        self.assertEqual(score['label'], 'fail')
        self.assertFalse(score['checks']['has_readable_source'])
        self.assertFalse(score['checks']['final_report_cites_source_ids'])

    def test_score_research_payload_uses_citation_audit(self) -> None:
        score = score_research_payload(
            {
                'ok': True,
                'sources': [{'source_id': 1, 'final_url': 'https://example.com'}],
                'citation_validation': {'ok': True, 'citation_count': 1, 'invalid_citations': []},
                'citation_audit': {'ok': False, 'issues': ['Report cites unknown source IDs: [99].']},
                'research_quality': {'label': 'moderate', 'score': 55},
                'recommended_next_searches': ['topic official source'],
                'final_report': 'Uses source:99.',
            }
        )

        self.assertFalse(score['checks']['citation_audit_passes'])
        self.assertEqual(score['metrics']['citation_audit_issue_count'], 1)

    def test_score_research_payload_uses_coverage_source_quality_and_freshness(self) -> None:
        score = score_research_payload(
            {
                'ok': True,
                'sources': [{'source_id': 1, 'final_url': 'https://example.com'}],
                'citation_validation': {'ok': True, 'citation_count': 1, 'invalid_citations': []},
                'research_coverage': {'missing_intents': ['primary_source']},
                'source_quality': {
                    'primary_source_count': 0,
                    'credibility_label_counts': {'low': 1},
                    'average_credibility_score': 20.0,
                },
                'source_freshness': {'gaps': ['No recent-change evidence snippets were extracted.']},
                'research_quality': {'label': 'thin', 'score': 35},
                'recommended_next_searches': ['topic official source'],
                'final_report': 'Uses source:1.',
            }
        )

        self.assertTrue(score['checks']['coverage_audit_present'])
        self.assertFalse(score['checks']['coverage_intents_satisfied'])
        self.assertFalse(score['checks']['has_primary_source'])
        self.assertFalse(score['checks']['has_high_or_medium_credibility'])
        self.assertFalse(score['checks']['freshness_audit_passes'])
        self.assertEqual(score['metrics']['coverage_missing_intent_count'], 1)
        self.assertEqual(score['metrics']['credibility_label_counts'], {'low': 1})
        self.assertEqual(score['metrics']['average_credibility_score'], 20.0)
        self.assertEqual(score['metrics']['freshness_gap_count'], 1)

    def test_score_research_payload_uses_final_answer_review(self) -> None:
        score = score_research_payload(
            {
                'ok': True,
                'sources': [{'source_id': 1, 'final_url': 'https://example.com'}],
                'citation_validation': {'ok': True, 'citation_count': 1, 'invalid_citations': []},
                'research_quality': {'label': 'moderate', 'score': 55},
                'recommended_next_searches': ['topic official source'],
                'final_answer_review': {'ok': False, 'issue_count': 2, 'high_count': 1, 'critical_count': 0},
                'final_report': 'Uses source:1.',
            }
        )

        self.assertFalse(score['checks']['final_answer_review_passes'])
        self.assertEqual(score['metrics']['final_answer_review_issue_count'], 2)
        self.assertEqual(score['metrics']['final_answer_review_high_count'], 1)
        self.assertLess(score['score'], 80)
        self.assertTrue(any(item['reason'] == 'final_answer_review_failed' for item in score['score_caps']))

    def test_score_research_payload_rewards_deep_research_quality_signals(self) -> None:
        score = score_research_payload(
            {
                'ok': True,
                'sources': [
                    {'source_id': 1, 'final_url': 'https://docs.example.com/a'},
                    {'source_id': 2, 'final_url': 'https://agency.gov/b'},
                    {'source_id': 3, 'final_url': 'https://news.example.org/c'},
                ],
                'citation_validation': {'ok': True, 'citation_count': 3, 'invalid_citations': []},
                'citation_audit': {'ok': True, 'issues': []},
                'research_coverage': {
                    'missing_intents': [],
                    'average_intent_quality_score': 78.5,
                    'low_quality_intents': [],
                },
                'source_quality': {
                    'primary_source_count': 2,
                    'credibility_label_counts': {'high': 2, 'medium': 1},
                    'average_credibility_score': 82,
                },
                'source_freshness': {'gaps': []},
                'research_quality': {'label': 'strong', 'score': 85},
                'evidence_index': {'ok': True, 'chunk_count': 6},
                'claim_support': {
                    'supported_claim_count': 2,
                    'unsupported_claim_count': 0,
                    'multi_source_supported_claim_count': 1,
                },
                'claims': [{'claim': 'A'}, {'claim': 'B'}],
                'final_answer_review': {
                    'ok': True,
                    'issue_count': 0,
                    'high_count': 0,
                    'critical_count': 0,
                    'contradiction_review': {'conflicted_claim_count': 0, 'retrieval_plan': []},
                },
                'agent_loop': {'rounds': [{'round': 1}], 'decisions': [{'decision': 'searched'}]},
                'recommended_next_searches': ['topic official source'],
                'final_report': '## Best Evidence\nUses source:1 and source:2.',
            },
            {'required_checks': ['claim_support_present', 'intent_quality_adequate']},
        )

        self.assertEqual(score['label'], 'pass')
        self.assertTrue(score['checks']['claim_support_present'])
        self.assertTrue(score['checks']['intent_quality_adequate'])
        self.assertEqual(score['required_check_failures'], [])
        self.assertEqual(score['metrics']['indexed_supported_claim_count'], 2)
        self.assertEqual(score['metrics']['average_intent_quality_score'], 78.5)

    def test_score_research_payload_scores_contradiction_table_details(self) -> None:
        score = score_research_payload(
            {
                'ok': True,
                'sources': [
                    {'source_id': 1, 'final_url': 'https://docs.example.com/a'},
                    {'source_id': 2, 'final_url': 'https://github.com/example/issues/1'},
                    {'source_id': 3, 'final_url': 'https://news.example.org/c'},
                ],
                'citation_validation': {'ok': True, 'citation_count': 3, 'invalid_citations': []},
                'research_quality': {'label': 'moderate', 'score': 65},
                'claims': [{'claim_id': 7, 'claim': 'Browser automation works by default.'}],
                'claim_support': {'supported_claim_count': 1, 'unsupported_claim_count': 0},
                'final_answer_review': {
                    'ok': False,
                    'issue_count': 1,
                    'high_count': 1,
                    'critical_count': 0,
                    'contradiction_review': {
                        'conflicted_claim_count': 1,
                        'retrieval_plan': [{'claim_id': 7, 'query': 'browser automation default official clarification'}],
                    },
                },
                'contradiction_table': {
                    'conflicted_claim_count': 1,
                    'rows': [
                        {
                            'claim_id': 7,
                            'claim': 'Browser automation works by default.',
                            'supporting_sources': [1],
                            'conflicting_sources': [2],
                            'retrieval_queries': ['browser automation default official clarification'],
                            'resolution_status': 'needs_resolution',
                        }
                    ],
                },
                'recommended_next_searches': ['topic official source'],
                'final_report': '## Source-Claim Contradiction Table\nUses source:1 and source:2.',
            },
            {'required_checks': ['contradiction_table_rows_present', 'contradiction_table_resolution_queries_present']},
        )

        self.assertTrue(score['checks']['contradiction_table_reported'])
        self.assertTrue(score['checks']['contradiction_table_rows_present'])
        self.assertTrue(score['checks']['contradiction_table_source_pairs_present'])
        self.assertTrue(score['checks']['contradiction_table_resolution_queries_present'])
        self.assertEqual(score['required_check_failures'], [])
        self.assertEqual(score['metrics']['contradiction_table_row_count'], 1)
        self.assertEqual(score['metrics']['contradiction_table_resolution_query_count'], 1)
        self.assertEqual(score['metrics']['contradiction_table_needs_resolution_count'], 1)

    def test_score_research_payload_penalizes_missing_contradiction_table_rows(self) -> None:
        score = score_research_payload(
            {
                'ok': True,
                'sources': [{'source_id': 1, 'final_url': 'https://example.com'}],
                'citation_validation': {'ok': True, 'citation_count': 1, 'invalid_citations': []},
                'research_quality': {'label': 'thin', 'score': 35},
                'final_answer_review': {
                    'ok': False,
                    'issue_count': 1,
                    'high_count': 1,
                    'critical_count': 0,
                    'contradiction_review': {'conflicted_claim_count': 1, 'retrieval_plan': []},
                },
                'contradiction_table': {'conflicted_claim_count': 1, 'rows': []},
                'final_report': 'Uses source:1.',
            },
            {
                'required_checks': [
                    'contradiction_table_rows_present',
                    'contradiction_table_source_pairs_present',
                    'contradiction_table_resolution_queries_present',
                ]
            },
        )

        self.assertFalse(score['checks']['contradiction_table_rows_present'])
        self.assertFalse(score['checks']['contradiction_table_source_pairs_present'])
        self.assertFalse(score['checks']['contradiction_table_resolution_queries_present'])
        self.assertIn('contradiction_table_rows_present', score['required_check_failures'])
        self.assertEqual(score['metrics']['contradiction_table_row_count'], 0)
        self.assertEqual(score['label'], 'fail')
        self.assertTrue(any(item['reason'] == 'required_check_failure' for item in score['score_caps']))

    def test_score_research_payload_caps_unresolved_conflicted_claims(self) -> None:
        score = score_research_payload(
            {
                'ok': True,
                'sources': [
                    {'source_id': 1, 'final_url': 'https://docs.example.com/a'},
                    {'source_id': 2, 'final_url': 'https://security.example.org/b'},
                    {'source_id': 3, 'final_url': 'https://news.example.org/c'},
                ],
                'citation_validation': {'ok': True, 'citation_count': 3, 'invalid_citations': []},
                'citation_audit': {'ok': True, 'issues': []},
                'research_coverage': {'missing_intents': [], 'average_intent_quality_score': 80, 'low_quality_intents': []},
                'source_quality': {'primary_source_count': 2, 'credibility_label_counts': {'high': 2}},
                'source_freshness': {'gaps': []},
                'research_quality': {'label': 'strong', 'score': 90},
                'evidence_index': {'ok': True, 'chunk_count': 5},
                'claims': [{'claim_id': 1, 'claim': 'A disputed claim.', 'supporting_sources': [1], 'conflicting_sources': [2]}],
                'claim_support': {
                    'supported_claim_count': 1,
                    'unsupported_claim_count': 0,
                    'multi_source_supported_claim_count': 1,
                },
                'final_answer_review': {
                    'ok': True,
                    'issue_count': 0,
                    'high_count': 0,
                    'critical_count': 0,
                    'contradiction_review': {
                        'conflicted_claim_count': 1,
                        'retrieval_plan': [{'claim_id': 1, 'query': 'disputed claim official clarification'}],
                    },
                },
                'contradiction_table': {
                    'rows': [
                        {
                            'claim_id': 1,
                            'supporting_sources': [1],
                            'conflicting_sources': [2],
                            'retrieval_queries': ['disputed claim official clarification'],
                            'resolution_status': 'needs_resolution',
                        }
                    ]
                },
                'agent_loop': {'rounds': [{'round': 1}], 'decisions': [{'decision': 'reviewed'}]},
                'recommended_next_searches': ['topic official source'],
                'final_report': '## Best Evidence\n## Source-Claim Contradiction Table\nUses source:1 and source:2.',
            }
        )

        self.assertFalse(score['checks']['contradiction_resolution_searched'])
        self.assertLess(score['score'], 80)
        self.assertEqual(score['label'], 'borderline')
        self.assertTrue(any(item['reason'] == 'conflicted_claims_not_resolution_searched' for item in score['score_caps']))

    def test_score_research_payload_penalizes_required_check_failures(self) -> None:
        score = score_research_payload(
            {
                'ok': True,
                'sources': [{'source_id': 1, 'final_url': 'https://example.com'}],
                'citation_validation': {'ok': True, 'citation_count': 1, 'invalid_citations': []},
                'research_quality': {'label': 'thin', 'score': 35},
                'claims': [{'claim': 'Unsupported'}],
                'claim_support': {'supported_claim_count': 0, 'unsupported_claim_count': 1},
                'final_report': 'Uses source:1.',
            },
            {'required_checks': ['indexed_claim_support', 'no_unsupported_indexed_claims']},
        )

        self.assertIn('indexed_claim_support', score['required_check_failures'])
        self.assertIn('no_unsupported_indexed_claims', score['required_check_failures'])
        self.assertIn('indexed_claim_support', score['weakest_checks'])

    def test_human_review_template_includes_review_axes(self) -> None:
        record = build_eval_record(
            {'id': 'task-one', 'category': 'technical', 'question': 'Question?'},
            {
                'ok': True,
                'sources': [{'source_id': 1, 'final_url': 'https://example.com'}],
                'citation_validation': {'ok': True, 'citation_count': 1},
                'research_quality': {'label': 'thin', 'score': 33},
                'final_report': 'source:1',
            },
            elapsed_seconds=1.25,
        )

        template = human_review_template(record)

        self.assertIn('Answer correctness', template)
        self.assertIn('Missing obvious sources', template)
        self.assertIn('Claim support:', template)
        self.assertIn('Intent quality:', template)
        self.assertIn('task-one', template)
        self.assertIn('table row', template)


if __name__ == '__main__':
    unittest.main()
