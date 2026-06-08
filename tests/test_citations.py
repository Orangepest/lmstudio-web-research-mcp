from __future__ import annotations

import unittest

from web_research.citations import audit_citations, validate_citations


class CitationAuditTests(unittest.TestCase):
    def test_validate_citations_rejects_unknown_sources(self) -> None:
        result = validate_citations({'sources': [{'source_id': 1}], 'citations': ['source:1[0:5]', 'source:9[0:5]']})

        self.assertFalse(result['ok'])
        self.assertEqual(result['invalid_citations'], ['source:9[0:5]'])

    def test_audit_citations_maps_claim_evidence_and_report_sources(self) -> None:
        payload = {
            'sources': [{'source_id': 1}, {'source_id': 2}],
            'citations': ['source:1[0:10]', 'source:2[0:10]'],
            'claims': [
                {
                    'claim_id': 1,
                    'claim': 'Claim one.',
                    'supporting_sources': [1, 2],
                    'supporting_evidence': [{'citation': 'source:1[0:10]'}],
                },
                {
                    'claim_id': 2,
                    'claim': 'Claim two.',
                    'supporting_sources': [2],
                    'supporting_evidence': [],
                },
            ],
            'citation_validation': {'ok': True, 'citation_count': 2, 'invalid_citations': []},
        }

        audit = audit_citations(
            payload,
            report='## What We Know\nClaim one source:1.\n\n## Extra Analysis\nUnsupported prose with no source marker and enough detail to require review.',
        )

        self.assertFalse(audit['ok'])
        self.assertEqual(audit['uncited_claim_ids'], [2])
        self.assertEqual(audit['claim_citation_map'][0]['missing_citation_source_ids'], [2])
        self.assertEqual(audit['report_cited_source_ids'], [1])
        self.assertIn('Extra Analysis', audit['unsupported_report_sections'])

    def test_audit_citations_detects_unknown_report_source_ids(self) -> None:
        audit = audit_citations(
            {
                'sources': [{'source_id': 1}],
                'citations': ['source:1[0:10]'],
                'claims': [],
                'citation_validation': {'ok': True, 'citation_count': 1, 'invalid_citations': []},
            },
            report='The answer cites source:99.',
        )

        self.assertFalse(audit['ok'])
        self.assertEqual(audit['unknown_report_source_ids'], [99])

    def test_audit_citations_allows_final_answer_review_without_source_marker(self) -> None:
        audit = audit_citations(
            {
                'sources': [{'source_id': 1}],
                'citations': ['source:1[0:10]'],
                'claims': [],
                'citation_validation': {'ok': True, 'citation_count': 1, 'invalid_citations': []},
            },
            report='## Final Answer Review\nThis section lists reviewer warnings and next-step fixes without source markers.',
        )

        self.assertTrue(audit['ok'])
        self.assertEqual(audit['unsupported_report_sections'], [])


if __name__ == '__main__':
    unittest.main()
