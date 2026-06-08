from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web_research.director import _director_remediation_strategy_upgrades, _remediation_strategy_learning


@dataclass(frozen=True)
class RemediationBenchmarkScenario:
    id: str
    description: str
    gap_code: str
    current_outcome: dict[str, Any]
    history: list[dict[str, Any]]
    expected_strategy: str
    expected_query_terms: tuple[str, ...] = ()


def _upgrade_history(
    *,
    gap_code: str,
    strategy: str,
    outcome: str,
    count: int,
    source_prefix: str,
) -> list[dict[str, Any]]:
    return [
        {
            'job_id': f'{source_prefix}-{index}',
            'target_gap': gap_code,
            'outcome': outcome,
            'is_upgrade': True,
            'strategy': strategy,
            'source_run_id': f'{source_prefix}-run',
            'source_count': 1 if outcome == 'resolved' else 0,
        }
        for index in range(count)
    ]


def remediation_learning_benchmark_scenarios() -> list[RemediationBenchmarkScenario]:
    return [
        RemediationBenchmarkScenario(
            id='missing-primary-learned-official-site',
            description='A hard primary-source gap should reuse learned official-site searches instead of the static primary-source-only default.',
            gap_code='missing_primary',
            history=[
                *_upgrade_history(
                    gap_code='missing_primary',
                    strategy='official_site_search',
                    outcome='resolved',
                    count=5,
                    source_prefix='official',
                ),
                *_upgrade_history(
                    gap_code='missing_primary',
                    strategy='primary_source_only',
                    outcome='remaining',
                    count=2,
                    source_prefix='primary',
                ),
            ],
            current_outcome={
                'job_id': 'current-missing-primary',
                'target_gap': 'missing_primary',
                'outcome': 'remaining',
                'is_upgrade': False,
                'request': 'senior dating monetization evidence benchmark',
                'source_run_id': None,
            },
            expected_strategy='official_site_search',
            expected_query_terms=('site:gov', 'official site'),
        ),
        RemediationBenchmarkScenario(
            id='failed-upgrade-switches-away-from-repeat',
            description='A failed upgrade should not retry the same strategy when another viable strategy exists.',
            gap_code='missing_primary',
            history=[],
            current_outcome={
                'job_id': 'failed-primary-source-only',
                'target_gap': 'missing_primary',
                'outcome': 'remaining',
                'is_upgrade': True,
                'strategy': 'primary_source_only',
                'request': 'market sizing primary evidence benchmark',
                'source_run_id': None,
            },
            expected_strategy='official_site_search',
            expected_query_terms=('official site',),
        ),
        RemediationBenchmarkScenario(
            id='conflict-learning-prefers-timeline-reconciliation',
            description='When timeline reconciliation has repeatedly resolved conflicts, it should beat the static conflict-resolution default.',
            gap_code='unresolved_conflicts',
            history=[
                *_upgrade_history(
                    gap_code='unresolved_conflicts',
                    strategy='timeline_reconciliation',
                    outcome='resolved',
                    count=5,
                    source_prefix='timeline',
                ),
                *_upgrade_history(
                    gap_code='unresolved_conflicts',
                    strategy='conflict_resolution',
                    outcome='remaining',
                    count=1,
                    source_prefix='conflict',
                ),
            ],
            current_outcome={
                'job_id': 'current-unresolved-conflict',
                'target_gap': 'unresolved_conflicts',
                'outcome': 'remaining',
                'is_upgrade': False,
                'request': 'conflicting benchmark claims official correction chronology',
                'source_run_id': None,
            },
            expected_strategy='timeline_reconciliation',
            expected_query_terms=('timeline', 'chronology'),
        ),
        RemediationBenchmarkScenario(
            id='pending-upgrade-dedupes-same-target',
            description='A pending repeated-domain repair for the same target should cause the next plan to choose the alternate corroboration strategy.',
            gap_code='repeated_domains',
            history=[
                {
                    'job_id': 'pending-domain-diversification',
                    'target_gap': 'repeated_domains',
                    'outcome': 'pending',
                    'is_upgrade': True,
                    'strategy': 'domain_diversification',
                    'source_run_id': 'source-run-1',
                }
            ],
            current_outcome={
                'job_id': 'current-repeated-domain',
                'target_gap': 'repeated_domains',
                'outcome': 'remaining',
                'is_upgrade': False,
                'request': 'same-domain source mix benchmark independent evidence',
                'source_run_id': 'source-run-1',
            },
            expected_strategy='cross_source_corroboration',
            expected_query_terms=('corroborating evidence', 'unrelated sources'),
        ),
    ]


def run_remediation_learning_benchmark(*, runs_root: Path | None = None) -> dict[str, Any]:
    root = runs_root or Path('.runtime') / 'remediation_benchmark_runs'
    scenarios = remediation_learning_benchmark_scenarios()
    records = []
    passed = 0
    for scenario in scenarios:
        learning = _remediation_strategy_learning({'outcomes': scenario.history})
        upgrades = _director_remediation_strategy_upgrades(
            {'outcomes': [*scenario.history, scenario.current_outcome]},
            runs_root=root,
            learning=learning,
            limit=4,
        )
        first = upgrades[0] if upgrades else {}
        query = str(first.get('query') or '')
        failures = []
        if first.get('strategy') != scenario.expected_strategy:
            failures.append(
                {
                    'type': 'strategy',
                    'expected': scenario.expected_strategy,
                    'actual': first.get('strategy'),
                }
            )
        for term in scenario.expected_query_terms:
            if term not in query:
                failures.append({'type': 'query_term', 'expected': term, 'actual_query': query})
        ok = not failures
        if ok:
            passed += 1
        records.append(
            {
                'id': scenario.id,
                'ok': ok,
                'description': scenario.description,
                'gap_code': scenario.gap_code,
                'expected_strategy': scenario.expected_strategy,
                'actual_strategy': first.get('strategy'),
                'actual_priority': first.get('priority'),
                'learned_priority_delta': first.get('learned_priority_delta'),
                'query': query,
                'failure_count': len(failures),
                'failures': failures,
                'learning_strategy_count': learning.get('strategy_count', 0),
            }
        )
    return {
        'ok': passed == len(scenarios),
        'scenario_count': len(scenarios),
        'passed': passed,
        'failed': len(scenarios) - passed,
        'records': records,
    }
