from __future__ import annotations

import unittest

from web_research.coverage import build_research_coverage
from web_research.planner import QueryPlanItem


class CoverageTests(unittest.TestCase):
    def test_build_research_coverage_tracks_satisfied_and_missing_intents(self) -> None:
        coverage = build_research_coverage(
            query_plan=[
                QueryPlanItem('topic', 'baseline', 'Baseline search.'),
                QueryPlanItem('topic official', 'primary_source', 'Primary search.'),
            ],
            searches=[
                {'query': 'topic', 'intent': 'baseline', 'ok': True},
                {'query': 'topic official', 'intent': 'primary_source', 'ok': False},
            ],
            selection_trace=[
                {'intent': 'baseline', 'decision': 'selected', 'source_id': 1},
                {'intent': 'primary_source', 'decision': 'read_failed'},
            ],
            source_quality={'selected_source_count': 1, 'unique_domain_count': 1, 'primary_source_count': 0},
            query='topic',
        )

        self.assertEqual(coverage['planned_intent_count'], 2)
        self.assertEqual(coverage['satisfied_intent_count'], 1)
        self.assertIn('primary_source', coverage['missing_intents'])
        self.assertEqual(coverage['by_intent'][0]['status'], 'satisfied')
        self.assertEqual(coverage['by_intent'][0]['quality_label'], 'thin')
        self.assertEqual(coverage['by_intent'][1]['status'], 'attempted_no_sources')
        self.assertEqual(coverage['by_intent'][1]['quality_label'], 'weak')
        self.assertIn('No strong primary source was selected.', coverage['gaps'])
        self.assertIn('primary_source', coverage['low_quality_intents'])

    def test_build_research_coverage_handles_single_query(self) -> None:
        coverage = build_research_coverage(
            searches=[{'query': 'topic', 'intent': 'single_search', 'ok': True}],
            selection_trace=[{'decision': 'selected', 'source_id': 1}],
            source_quality={'selected_source_count': 1, 'unique_domain_count': 2, 'primary_source_count': 1},
            query='topic',
        )

        self.assertEqual(coverage['planned_intent_count'], 1)
        self.assertEqual(coverage['missing_intents'], [])
        self.assertGreaterEqual(coverage['average_intent_quality_score'], 25)

    def test_build_research_coverage_requires_matching_sources_for_specialized_intents(self) -> None:
        coverage = build_research_coverage(
            query_plan=[QueryPlanItem('topic official', 'primary_source', 'Find primary evidence.')],
            searches=[{'query': 'topic official', 'intent': 'primary_source', 'ok': True}],
            selection_trace=[
                {
                    'intent': 'primary_source',
                    'decision': 'selected',
                    'source_id': 1,
                    'url': 'https://forum.example/topic',
                    'source_type': 'forum',
                    'reliability_weight': 'supporting',
                }
            ],
            source_quality={'selected_source_count': 1, 'unique_domain_count': 1, 'primary_source_count': 0},
            query='topic',
        )

        self.assertEqual(coverage['by_intent'][0]['status'], 'selected_unmatched')
        self.assertEqual(coverage['by_intent'][0]['matched_source_count'], 0)
        self.assertEqual(coverage['by_intent'][0]['selected_unmatched_count'], 1)
        self.assertLess(coverage['by_intent'][0]['quality_score'], 50)
        self.assertIn('primary_source', coverage['missing_intents'])

    def test_build_research_coverage_accepts_official_matches_for_primary_intents(self) -> None:
        coverage = build_research_coverage(
            query_plan=[QueryPlanItem('topic official', 'primary_source', 'Find primary evidence.')],
            searches=[{'query': 'topic official', 'intent': 'primary_source', 'ok': True}],
            selection_trace=[
                {
                    'intent': 'primary_source',
                    'decision': 'selected',
                    'source_id': 1,
                    'url': 'https://agency.gov/topic',
                    'source_type': 'government',
                    'reliability_weight': 'strong',
                }
            ],
            source_quality={'selected_source_count': 1, 'unique_domain_count': 1, 'primary_source_count': 1},
            query='topic',
        )

        self.assertEqual(coverage['by_intent'][0]['status'], 'satisfied')
        self.assertEqual(coverage['by_intent'][0]['matched_source_count'], 1)
        self.assertGreaterEqual(coverage['by_intent'][0]['quality_score'], 50)
        self.assertIn('strong_source', coverage['by_intent'][0]['quality_signals'])
        self.assertEqual(coverage['missing_intents'], [])

    def test_build_research_coverage_scores_per_intent_source_quality(self) -> None:
        coverage = build_research_coverage(
            query_plan=[
                QueryPlanItem('topic docs', 'documentation', 'Find docs.'),
                QueryPlanItem('topic limits', 'counterpoint', 'Find caveats.'),
            ],
            searches=[
                {'query': 'topic docs', 'intent': 'documentation', 'ok': True},
                {'query': 'topic limits', 'intent': 'counterpoint', 'ok': True},
            ],
            selection_trace=[
                {
                    'intent': 'documentation',
                    'decision': 'selected',
                    'source_id': 1,
                    'url': 'https://docs.example.com/topic',
                    'source_type': 'documentation',
                    'reliability_weight': 'strong',
                    'source_score_reasons': ['documentation_or_repository', 'primary_source_hint'],
                    'source_intent_score': 45,
                    'source_intent_reasons': ['intent_documentation'],
                },
                {
                    'intent': 'counterpoint',
                    'decision': 'selected',
                    'source_id': 2,
                    'url': 'https://blog.example.com/topic',
                    'source_type': 'blog',
                    'reliability_weight': 'supporting',
                    'source_score_reasons': [],
                },
            ],
            source_quality={'selected_source_count': 2, 'unique_domain_count': 2, 'primary_source_count': 1},
            query='topic',
        )

        docs = next(item for item in coverage['by_intent'] if item['intent'] == 'documentation')
        counterpoint = next(item for item in coverage['by_intent'] if item['intent'] == 'counterpoint')

        self.assertEqual(docs['quality_label'], 'strong')
        self.assertGreater(docs['quality_score'], counterpoint['quality_score'])
        self.assertIn('intent_documentation', docs['quality_signals'])
        self.assertIn('counterpoint', coverage['missing_intents'])
        self.assertIn('counterpoint', coverage['low_quality_intents'])


if __name__ == '__main__':
    unittest.main()
