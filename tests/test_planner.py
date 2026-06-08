from __future__ import annotations

import unittest

from web_research.planner import append_terms_once, build_query_plan, classify_topic, plan_source_reads, rerank_search_results, score_search_result


class PlannerTests(unittest.TestCase):
    def test_build_query_plan_returns_labeled_unique_queries(self) -> None:
        plan = build_query_plan('LM Studio MCP', breadth=4)

        self.assertEqual([item.intent for item in plan], ['baseline', 'primary_source', 'documentation', 'known_official_site'])
        self.assertEqual(plan[0].query, 'LM Studio MCP')
        self.assertEqual(plan[1].query, 'LM Studio MCP official source')
        self.assertTrue(all(item.rationale for item in plan))

    def test_append_terms_once_avoids_duplicate_words(self) -> None:
        self.assertEqual(append_terms_once('LM Studio MCP documentation', 'documentation'), 'LM Studio MCP documentation')
        self.assertEqual(append_terms_once('LM Studio MCP', 'documentation'), 'LM Studio MCP documentation')

    def test_build_query_plan_adds_repository_only_when_requested(self) -> None:
        docs_plan = build_query_plan('LM Studio MCP documentation', breadth=6)
        repo_plan = build_query_plan('LM Studio MCP GitHub repository', breadth=6)

        self.assertNotIn('repository', [item.intent for item in docs_plan])
        self.assertTrue(any(item.intent == 'repository' and item.site == 'github.com' for item in repo_plan))

    def test_build_query_plan_adds_repository_for_local_tool_comparisons(self) -> None:
        plan = build_query_plan('Compare local AI coding assistant options for developers', breadth=3)

        self.assertTrue(any(item.intent == 'repository' and item.site == 'github.com' for item in plan))

    def test_build_query_plan_adds_known_official_sites(self) -> None:
        plan = build_query_plan('Should a local assistant use SearXNG or a paid search API?', breadth=4)

        self.assertTrue(any(item.intent == 'known_official_site' and item.site == 'docs.searxng.org' for item in plan))

    def test_classify_topic_detects_regulatory_questions(self) -> None:
        profile = classify_topic('FTC auto renewal compliance policy')

        self.assertEqual(profile.kind, 'regulatory')
        self.assertIn('.gov', profile.preferred_sites)

    def test_classify_topic_sets_market_authority_preset(self) -> None:
        profile = classify_topic('highest ROI dating app revenue features and market data')

        self.assertEqual(profile.kind, 'market')
        self.assertEqual(profile.authority_preset, 'market')

    def test_build_query_plan_uses_site_constraints_for_regulatory_topics(self) -> None:
        plan = build_query_plan('FTC subscription cancellation rule', breadth=4)

        self.assertTrue(any(item.site == '.gov' for item in plan))
        self.assertTrue(any(item.site == 'federalregister.gov' for item in plan))
        self.assertIn('government_source', [item.intent for item in plan])

    def test_score_search_result_rewards_primary_sources(self) -> None:
        official_score, official_reasons = score_search_result(
            {
                'title': 'Official API documentation',
                'url': 'https://docs.example.gov/api',
                'snippet': 'Developer documentation and dataset.',
                'rank': 3,
            }
        )
        generic_score, generic_reasons = score_search_result(
            {
                'title': 'Top 10 best tools',
                'url': 'https://medium.com/example/post',
                'snippet': 'Sponsored ultimate guide.',
                'rank': 1,
            }
        )

        self.assertGreater(official_score, generic_score)
        self.assertIn('primary_tld', official_reasons)
        self.assertIn('low_value_hint', generic_reasons)

    def test_score_search_result_penalizes_source_policy_skips(self) -> None:
        skipped_score, skipped_reasons = score_search_result(
            {
                'title': 'ResearchGate paper',
                'url': 'https://www.researchgate.net/publication/123',
                'snippet': 'research paper official looking result',
                'rank': 1,
            }
        )
        readable_score, readable_reasons = score_search_result(
            {
                'title': 'Company report',
                'url': 'https://company.example/report',
                'snippet': 'official report',
                'rank': 5,
            }
        )

        self.assertLess(skipped_score, readable_score)
        self.assertIn('source_policy_skip:hostile_or_low_value_research_domain', skipped_reasons)
        self.assertNotIn('source_policy_skip:hostile_or_low_value_research_domain', readable_reasons)

    def test_rerank_search_results_limits_repeated_domains(self) -> None:
        results = [
            {'title': 'A docs', 'url': 'https://same.example/docs/a', 'snippet': '', 'rank': 1},
            {'title': 'B docs', 'url': 'https://same.example/docs/b', 'snippet': '', 'rank': 2},
            {'title': 'C docs', 'url': 'https://same.example/docs/c', 'snippet': '', 'rank': 3},
            {'title': 'Official report', 'url': 'https://other.gov/report', 'snippet': '', 'rank': 4},
        ]

        ranked = rerank_search_results(results, per_domain_limit=1)

        self.assertEqual(ranked[0]['url'], 'https://other.gov/report')
        self.assertLess(ranked.index(next(item for item in ranked if item['url'].endswith('/c'))), len(ranked))
        self.assertEqual([item['rank'] for item in ranked], [1, 2, 3, 4])

    def test_rerank_search_results_pushes_policy_skips_below_readable_results(self) -> None:
        results = [
            {'title': 'ResearchGate paper', 'url': 'https://www.researchgate.net/publication/123', 'snippet': 'official research report', 'rank': 1},
            {'title': 'Embedded PDF viewer', 'url': 'https://journal.example/plugins/generic/pdfJsViewer/pdf.js/web/viewer.html?file=x', 'snippet': 'pdf viewer', 'rank': 2},
            {'title': 'Official report', 'url': 'https://company.example/report', 'snippet': 'official report', 'rank': 5},
        ]

        ranked = rerank_search_results(results)

        self.assertEqual(ranked[0]['url'], 'https://company.example/report')
        self.assertTrue(ranked[-1]['source_score_reasons'][0] or ranked[-1]['source_score_reasons'])
        self.assertTrue(any(str(reason).startswith('source_policy_skip:') for reason in ranked[-1]['source_score_reasons']))

    def test_rerank_search_results_uses_market_authority_preset(self) -> None:
        results = [
            {
                'title': 'Top 7 Successful Revenue Models to Monetize Your Dating App',
                'url': 'https://www.code-brew.com/top-7-revenue-models-to-successfully-monetize-your-dating-app',
                'snippet': 'Dating app development company guide for build an app and hire developers.',
                'rank': 1,
            },
            {
                'title': 'Match Group annual report 10-K revenue',
                'url': 'https://www.sec.gov/Archives/edgar/data/891103/match-10k.htm',
                'snippet': 'SEC filing annual report with revenue breakdown and shareholder data.',
                'rank': 2,
            },
            {
                'title': 'Tinder - Dating App on the App Store',
                'url': 'https://apps.apple.com/us/app/tinder-dating-app/id547702041',
                'snippet': 'Official App Store listing with subscription and in-app purchase information.',
                'rank': 3,
            },
        ]

        ranked = rerank_search_results(results, query='dating app revenue breakdown market data Tinder subscription ROI')

        self.assertEqual(ranked[0]['url'], 'https://www.sec.gov/Archives/edgar/data/891103/match-10k.htm')
        self.assertEqual(ranked[1]['url'], 'https://apps.apple.com/us/app/tinder-dating-app/id547702041')
        self.assertIn('market_authority_domain', ranked[0]['source_score_reasons'])
        self.assertIn('market_primary_data_source', ranked[0]['source_score_reasons'])
        self.assertIn('market_low_value_hint', ranked[-1]['source_score_reasons'])
        self.assertEqual(ranked[0]['topic_profile']['authority_preset'], 'market')

    def test_plan_source_reads_reserves_room_for_strong_sources(self) -> None:
        results = rerank_search_results(
            [
                {'title': 'Forum discussion', 'url': 'https://forum.example/a', 'snippet': 'community comments', 'rank': 1},
                {'title': 'Blog guide', 'url': 'https://blog.example/b', 'snippet': 'ultimate guide', 'rank': 2},
                {'title': 'Official docs', 'url': 'https://docs.example.com/api', 'snippet': 'official documentation', 'rank': 3},
                {'title': 'Government report', 'url': 'https://agency.gov/report', 'snippet': 'official report', 'rank': 4},
            ]
        )

        planned = plan_source_reads(results, read_top=2, inspect_limit=4)

        self.assertEqual(len(planned), 4)
        self.assertTrue(all(item['read_selection_reason'] == 'strong_source_candidate' for item in planned[:2]))
        self.assertEqual([item['read_selection_rank'] for item in planned], [1, 2, 3, 4])
        self.assertEqual({planned[0]['url'], planned[1]['url']}, {'https://docs.example.com/api', 'https://agency.gov/report'})
        self.assertIn('domain_diversity', [item['read_selection_reason'] for item in planned])

    def test_plan_source_reads_marks_policy_skips_after_better_candidates(self) -> None:
        results = rerank_search_results(
            [
                {'title': 'ResearchGate paper', 'url': 'https://www.researchgate.net/publication/123', 'snippet': 'official paper report', 'rank': 1},
                {'title': 'Official report', 'url': 'https://company.example/report', 'snippet': 'official report', 'rank': 2},
                {'title': 'Docs', 'url': 'https://docs.example.com/guide', 'snippet': 'documentation', 'rank': 3},
            ]
        )

        planned = plan_source_reads(results, read_top=2, inspect_limit=3)

        self.assertEqual({planned[0]['url'], planned[1]['url']}, {'https://company.example/report', 'https://docs.example.com/guide'})
        self.assertEqual(planned[-1]['url'], 'https://www.researchgate.net/publication/123')
        self.assertEqual(planned[-1]['source_policy_skip_reason'], 'hostile_or_low_value_research_domain')

    def test_plan_source_reads_reserves_market_authority_sources(self) -> None:
        results = rerank_search_results(
            [
                {
                    'title': 'Dating app development cost guide',
                    'url': 'https://dedicateddevelopers.com/dating-app-development-cost-2025/',
                    'snippet': 'Hire developers to build your app.',
                    'rank': 1,
                },
                {
                    'title': 'Bumble investor relations quarterly results',
                    'url': 'https://ir.bumble.com/news-events/press-releases',
                    'snippet': 'Press release and earnings revenue data.',
                    'rank': 2,
                },
                {
                    'title': 'RevenueCat subscription app benchmarks',
                    'url': 'https://www.revenuecat.com/state-of-subscription-apps/',
                    'snippet': 'Subscription benchmark statistics and market report.',
                    'rank': 3,
                },
            ],
            query='dating app monetization market revenue subscription data',
        )

        planned = plan_source_reads(results, read_top=2, inspect_limit=3)

        self.assertEqual(
            {item['url'] for item in planned[:2]},
            {'https://ir.bumble.com/news-events/press-releases', 'https://www.revenuecat.com/state-of-subscription-apps/'},
        )
        self.assertTrue(all(item['read_selection_reason'] == 'strong_source_candidate' for item in planned[:2]))

    def test_plan_source_reads_uses_source_intent_before_generic_strength(self) -> None:
        results = rerank_search_results(
            [
                {'title': 'Official documentation', 'url': 'https://docs.example.com/api', 'snippet': 'official documentation', 'rank': 1},
                {'title': 'Release notes', 'url': 'https://blog.example.com/releases', 'snippet': 'latest changelog updated 2026', 'rank': 2},
                {'title': 'Repository', 'url': 'https://github.com/example/project', 'snippet': 'source code releases', 'rank': 3},
                {'title': 'Forum guide', 'url': 'https://forum.example.com/thread', 'snippet': 'community guide', 'rank': 4},
            ]
        )

        freshness = plan_source_reads(results, read_top=1, inspect_limit=4, source_intent='freshness')
        repository = plan_source_reads(results, read_top=1, inspect_limit=4, source_intent='repository')

        self.assertEqual(freshness[0]['url'], 'https://blog.example.com/releases')
        self.assertEqual(freshness[0]['read_selection_reason'], 'intent_match:freshness')
        self.assertIn('intent_freshness', freshness[0]['source_intent_reasons'])
        self.assertEqual(repository[0]['url'], 'https://github.com/example/project')
        self.assertEqual(repository[0]['read_selection_reason'], 'intent_match:repository')
        self.assertIn('intent_repository', repository[0]['source_intent_reasons'])

    def test_plan_source_reads_routes_contradiction_resolution_to_clarifying_sources(self) -> None:
        results = rerank_search_results(
            [
                {'title': 'Forum dispute', 'url': 'https://forum.example.com/thread', 'snippet': 'people argue about the issue', 'rank': 1},
                {'title': 'Official clarification', 'url': 'https://docs.example.com/clarification', 'snippet': 'official correction and evidence', 'rank': 2},
                {'title': 'Generic guide', 'url': 'https://blog.example.com/guide', 'snippet': 'overview', 'rank': 3},
            ]
        )

        planned = plan_source_reads(results, read_top=1, inspect_limit=3, source_intent='contradiction_resolution')

        self.assertEqual(planned[0]['url'], 'https://docs.example.com/clarification')
        self.assertEqual(planned[0]['read_selection_reason'], 'intent_match:contradiction_resolution')
        self.assertIn('intent_contradiction_resolution', planned[0]['source_intent_reasons'])


if __name__ == '__main__':
    unittest.main()
