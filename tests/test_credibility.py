from __future__ import annotations

import unittest

from web_research.credibility import credibility_assessment, normalize_domain


class CredibilityTests(unittest.TestCase):
    def test_credibility_assessment_rewards_government_and_docs(self) -> None:
        gov = credibility_assessment('https://agency.gov/rule', source_type='government')
        docs = credibility_assessment('https://docs.example.com/api', source_type='documentation')

        self.assertEqual(gov['label'], 'high')
        self.assertEqual(gov['entity_class'], 'government')
        self.assertGreaterEqual(gov['score'], 80)
        self.assertIn('Documentation source.', docs['reasons'])

    def test_credibility_assessment_penalizes_community_sources(self) -> None:
        reddit = credibility_assessment('https://reddit.com/r/localai/comments/1', source_type='forum')

        self.assertEqual(reddit['entity_class'], 'community')
        self.assertIn('Community/user-generated source.', reddit['caveats'])
        self.assertLess(reddit['score'], 50)

    def test_normalize_domain_strips_www(self) -> None:
        self.assertEqual(normalize_domain('https://www.example.com/path'), 'example.com')


if __name__ == '__main__':
    unittest.main()
