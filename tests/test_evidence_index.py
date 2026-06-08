from __future__ import annotations

import unittest

from web_research.evidence_index import build_evidence_index, tokenize


class EvidenceIndexTests(unittest.TestCase):
    def test_tokenize_removes_stopwords(self) -> None:
        self.assertEqual(tokenize('The local research index works'), ['local', 'index', 'works'])

    def test_build_evidence_index_scores_chunks_and_tracks_coverage(self) -> None:
        index = build_evidence_index(
            'local privacy retrieval',
            [
                {
                    'source_id': 1,
                    'title': 'Docs',
                    'final_url': 'https://docs.example.com',
                    'text': 'Local privacy research needs retrieval indexes. ' * 12,
                    'reliability': {'reliability_weight': 'strong', 'source_type': 'documentation'},
                },
                {
                    'source_id': 2,
                    'title': 'Blog',
                    'final_url': 'https://blog.example.com',
                    'text': 'Unrelated deployment notes. ' * 12,
                    'reliability': {'reliability_weight': 'supporting', 'source_type': 'blog'},
                },
            ],
            [{'source_id': 1, 'quote': 'Local privacy research needs retrieval indexes.'}],
            chunk_words=24,
        )

        self.assertTrue(index['ok'])
        self.assertGreaterEqual(index['chunk_count'], 2)
        self.assertEqual(index['top_chunks'][0]['source_id'], 1)
        self.assertIn('privacy', index['top_chunks'][0]['matched_terms'])
        self.assertEqual(index['coverage']['source_count'], 2)
        self.assertEqual(index['coverage']['evidence_source_count'], 1)
        self.assertIn('1', index['source_chunk_counts'])


if __name__ == '__main__':
    unittest.main()
