from __future__ import annotations

import unittest

from web_research.remediation import build_research_remediation_plan


class RemediationTests(unittest.TestCase):
    def test_build_research_remediation_plan_targets_evidence_gaps(self) -> None:
        plan = build_research_remediation_plan(
            {
                'question': 'dating app monetization ROI',
                'sources': [{'source_id': 1, 'url': 'https://seo.example/guide'}],
                'source_quality': {'selected_source_count': 1, 'unique_domain_count': 1, 'primary_source_count': 0},
                'claims': [
                    {'claim': 'Boosts are high ROI.', 'supporting_sources': [1]},
                    {'claim': 'Virtual gifts are disputed.', 'supporting_sources': [1], 'conflicting_sources': [2]},
                ],
                'citation_audit': {'ok': False, 'issues': ['uncited claim']},
                'source_freshness': {'current_sensitive': True, 'content_freshness_evidence': False},
                'source_selection_telemetry': {
                    'planned_low_value_source_count': 3,
                    'planned_authority_source_count': 1,
                    'selected_authority_source_count': 0,
                    'repeated_domains': {'seo.example': 3},
                },
            }
        )

        codes = [gap['code'] for gap in plan['gaps']]
        action_codes = [action['gap_code'] for action in plan['actions']]

        self.assertIn('unresolved_conflicts', codes)
        self.assertIn('missing_primary', codes)
        self.assertIn('seo_heavy_source_mix', codes)
        self.assertEqual(plan['actions'][0]['gap_code'], 'unresolved_conflicts')
        self.assertIn('authority_candidates_not_selected', action_codes)
        self.assertTrue(any('official' in action['query'] for action in plan['actions']))


if __name__ == '__main__':
    unittest.main()
