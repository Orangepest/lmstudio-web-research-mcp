from __future__ import annotations

import unittest

from web_research.review import adversarial_final_answer_review


class FinalAnswerReviewTests(unittest.TestCase):
    def test_adversarial_final_answer_review_flags_high_risk_answer(self) -> None:
        review = adversarial_final_answer_review(
            {
                'sources': [{'source_id': 1}],
                'claims': [
                    {'claim_id': 1, 'claim': 'Single sourced claim.', 'supporting_sources': [1]},
                    {'claim_id': 2, 'claim': 'Conflicted claim.', 'supporting_sources': [1], 'conflicting_sources': [2]},
                ],
                'source_quality': {'primary_source_count': 0, 'unique_domain_count': 1},
                'research_quality': {'label': 'thin', 'score': 35},
                'research_coverage': {'missing_intents': ['primary_source']},
                'source_freshness': {'gaps': ['No recent-change evidence snippets were extracted.']},
                'citation_audit': {'ok': False, 'issues': ['uncited claim']},
                'citation_validation': {'ok': True},
                'blocked_sources': [{'url': 'https://blocked.example'}],
                'recommended_next_searches': [],
            }
        )

        codes = [issue['code'] for issue in review['issues']]
        self.assertFalse(review['ok'])
        self.assertIn('coverage_gaps', codes)
        self.assertIn('no_primary_sources', codes)
        self.assertIn('conflicted_claims', codes)
        self.assertIn('missing_follow_up_plan', codes)
        self.assertGreaterEqual(review['high_count'], 1)
        self.assertEqual(review['contradiction_review']['conflicted_claim_count'], 1)
        self.assertEqual(review['contradiction_review']['contested_claims'][0]['claim_id'], 2)
        self.assertTrue(
            any('Conflicted claim' in search for search in review['contradiction_review']['follow_up_searches'])
        )
        self.assertEqual(review['contradiction_review']['retrieval_plan'][0]['intent'], 'contradiction_resolution')
        self.assertEqual(review['contradiction_review']['retrieval_plan'][0]['claim_id'], 2)
        self.assertIn('independent verification', review['contradiction_review']['retrieval_plan'][0]['query'])

    def test_adversarial_final_answer_review_passes_clean_answer(self) -> None:
        review = adversarial_final_answer_review(
            {
                'sources': [{'source_id': 1}, {'source_id': 2}],
                'claims': [{'claim_id': 1, 'claim': 'Supported claim.', 'supporting_sources': [1, 2]}],
                'source_quality': {'primary_source_count': 1, 'unique_domain_count': 2},
                'research_quality': {'label': 'strong', 'score': 82},
                'research_coverage': {'missing_intents': []},
                'source_freshness': {'gaps': []},
                'citation_audit': {'ok': True},
                'citation_validation': {'ok': True},
                'recommended_next_searches': ['topic official source'],
            }
        )

        self.assertTrue(review['ok'])
        self.assertEqual(review['issue_count'], 0)
        self.assertTrue(review['contradiction_review']['ok'])


if __name__ == '__main__':
    unittest.main()
