from __future__ import annotations

import unittest

from web_research.remediation_benchmarks import (
    remediation_learning_benchmark_scenarios,
    run_remediation_learning_benchmark,
)


class RemediationBenchmarkTests(unittest.TestCase):
    def test_benchmark_scenarios_are_repeatable_and_named(self) -> None:
        scenarios = remediation_learning_benchmark_scenarios()

        self.assertGreaterEqual(len(scenarios), 4)
        self.assertEqual(len({scenario.id for scenario in scenarios}), len(scenarios))
        self.assertTrue(all(scenario.expected_strategy for scenario in scenarios))

    def test_remediation_learning_benchmark_passes_expected_strategy_ranking(self) -> None:
        result = run_remediation_learning_benchmark()

        self.assertTrue(result['ok'])
        self.assertEqual(result['failed'], 0)
        by_id = {record['id']: record for record in result['records']}
        self.assertEqual(by_id['missing-primary-learned-official-site']['actual_strategy'], 'official_site_search')
        self.assertEqual(by_id['failed-upgrade-switches-away-from-repeat']['actual_strategy'], 'official_site_search')
        self.assertEqual(by_id['conflict-learning-prefers-timeline-reconciliation']['actual_strategy'], 'timeline_reconciliation')
        self.assertEqual(by_id['pending-upgrade-dedupes-same-target']['actual_strategy'], 'cross_source_corroboration')


if __name__ == '__main__':
    unittest.main()
