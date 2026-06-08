from __future__ import annotations

import unittest

from web_research.claim_support import build_claim_support_table


class ClaimSupportTests(unittest.TestCase):
    def test_build_claim_support_table_maps_claims_to_indexed_chunks(self) -> None:
        support = build_claim_support_table(
            [
                {
                    'claim_id': 7,
                    'claim': 'Local MCP research tools support source-grounded retrieval.',
                    'confidence': 'medium',
                    'supporting_sources': [1, 2],
                }
            ],
            {
                'ok': True,
                'top_chunks': [
                    {
                        'chunk_id': 'source:1:chunk:1',
                        'source_id': 1,
                        'score': 3.0,
                        'text': 'Local MCP research tools support retrieval with grounded evidence.',
                    },
                    {
                        'chunk_id': 'source:2:chunk:1',
                        'source_id': 2,
                        'score': 2.0,
                        'text': 'Source-grounded retrieval improves local research tools.',
                    },
                ],
            },
        )

        self.assertTrue(support['ok'])
        self.assertEqual(support['supported_claim_count'], 1)
        self.assertEqual(support['unsupported_claim_count'], 0)
        self.assertEqual(support['multi_source_supported_claim_count'], 1)
        self.assertEqual(support['claims'][0]['claim_id'], 7)
        self.assertEqual(support['claims'][0]['indexed_support_sources'], [1, 2])

    def test_build_claim_support_table_marks_unmatched_claims_as_gaps(self) -> None:
        support = build_claim_support_table(
            [{'claim': 'Unrelated claim about hidden behavior.', 'supporting_sources': [1]}],
            {'ok': True, 'top_chunks': [{'chunk_id': 'source:1:chunk:1', 'source_id': 1, 'text': 'Local docs mention setup.'}]},
        )

        self.assertFalse(support['ok'])
        self.assertEqual(support['unsupported_claim_count'], 1)
        self.assertEqual(support['claims'][0]['status'], 'needs_indexed_support')
        self.assertTrue(support['gaps'])


if __name__ == '__main__':
    unittest.main()
