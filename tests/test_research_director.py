from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.research_director import ROOT
from web_research.director import advance_research_director, build_director_evidence_graph, build_director_runbook, build_research_director_dashboard, compare_director_bundles, execute_director_graph_actions, export_director_runbook, research_director_command, run_research_director_autopilot, run_research_director_wave
from web_research.jobs import create_research_job, finish_research_job, lease_next_research_job, list_research_jobs, load_research_job, update_research_job
from web_research.runs import save_research_run


def _complete_one_director_job(root: Path, *, claim: str = 'Research directors can create follow-up work.') -> dict:
    saved = save_research_run(
        'deep_research',
        'director child query',
        {
            'ok': True,
            'final_report': f'# Director\n\n{claim}',
            'sources': [{'source_id': 1, 'title': 'Docs', 'final_url': 'https://example.com/docs'}],
            'claims': [{'claim_id': 1, 'claim': claim, 'supporting_sources': [1]}],
            'research_quality': {'label': 'weak', 'score': 42},
            'source_quality': {'label': 'limited', 'score': 40, 'primary_source_count': 0},
            'source_selection_telemetry': {
                'planned_low_value_source_count': 3,
                'planned_authority_source_count': 1,
                'selected_authority_source_count': 0,
            },
            'recommended_next_searches': ['official primary sources for research directors'],
        },
        root=root / 'runs',
    )
    leased = lease_next_research_job(root / 'jobs', worker_id='director-test')
    finish_research_job(
        root / 'jobs',
        leased['job']['job_id'],
        lease_id=leased['lease_id'],
        status='completed',
        event='completed',
        run_id=saved['run_id'],
    )
    return saved


def _complete_all_remaining_jobs(root: Path, *, quality_score: int = 82, primary_sources: int = 1) -> list[dict]:
    saved_runs = []
    while True:
        leased = lease_next_research_job(root / 'jobs', worker_id='director-test')
        if not leased.get('leased'):
            break
        saved = save_research_run(
            'deep_research',
            leased['job']['request_preview'],
            {
                'ok': True,
                'final_report': '# Director\n\nEvidence-backed result using source:1.',
                'sources': [{'source_id': 1, 'title': 'Docs', 'final_url': 'https://example.com/docs'}],
                'claims': [{'claim_id': 1, 'claim': 'Evidence-backed result.', 'supporting_sources': [1]}],
                'research_quality': {'label': 'strong', 'score': quality_score},
                'source_quality': {'label': 'strong', 'score': 80, 'primary_source_count': primary_sources},
                'source_selection_telemetry': {
                    'planned_authority_source_count': 2,
                    'selected_authority_source_count': 1,
                    'planned_low_value_source_count': 1,
                    'planned_policy_skip_count': 0,
                },
                'recommended_next_searches': [],
            },
            root=root / 'runs',
        )
        finish_research_job(
            root / 'jobs',
            leased['job']['job_id'],
            lease_id=leased['lease_id'],
            status='completed',
            event='completed',
            run_id=saved['run_id'],
        )
        saved_runs.append(saved)
    return saved_runs


def _complete_one_graph_issue_job(root: Path) -> dict:
    saved = save_research_run(
        'deep_research',
        'graph issue run',
        {
            'ok': True,
            'final_report': '# Graph\n\nClaim needs stronger support and contradiction review.',
            'sources': [
                {'source_id': 1, 'title': 'Docs A', 'final_url': 'https://repeat.example.com/a'},
                {'source_id': 2, 'title': 'Docs B', 'final_url': 'https://repeat.example.com/b'},
            ],
            'claims': [
                {'claim_id': 1, 'claim': 'Weak claim without support.', 'supporting_sources': []},
                {'claim_id': 2, 'claim': 'Disputed claim.', 'supporting_sources': [1], 'conflicting_sources': [2]},
            ],
            'contradiction_table': {
                'rows': [{'claim': 'Disputed claim.', 'status': 'unresolved'}],
            },
            'research_quality': {'label': 'weak', 'score': 42},
            'source_quality': {'label': 'limited', 'score': 40, 'primary_source_count': 0},
            'recommended_next_searches': [],
        },
        root=root / 'runs',
    )
    leased = lease_next_research_job(root / 'jobs', worker_id='director-test')
    finish_research_job(
        root / 'jobs',
        leased['job']['job_id'],
        lease_id=leased['lease_id'],
        status='completed',
        event='completed',
        run_id=saved['run_id'],
    )
    return saved


def _seed_shared_official_site_learning(root: Path) -> dict:
    created = research_director_command(
        'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=20\nquality_target=strong\napply=true',
        root=root / 'directors',
        campaign_root=root / 'campaigns',
        jobs_root=root / 'jobs',
        runs_root=root / 'runs',
        synthesis_root=root / 'syntheses',
    )
    director = created['director']
    director_id = director['director_id']
    source_run_id = _complete_all_remaining_jobs(root, quality_score=42, primary_sources=0)[0]['run_id']
    for index in range(5):
        resolved = save_research_run(
            'deep_research',
            f'shared official site success {index}',
            {
                'ok': True,
                'final_report': '# Resolved\n\nOfficial source resolved the missing primary gap.',
                'sources': [{'source_id': 1, 'title': 'Official Site', 'final_url': f'https://official.example.com/shared-{index}'}],
                'claims': [{'claim_id': 1, 'claim': 'Official source resolved the gap.', 'supporting_sources': [1]}],
                'research_quality': {'label': 'strong', 'score': 84},
                'source_quality': {'label': 'strong', 'score': 82, 'primary_source_count': 1},
                'remediation_plan': {'ok': True, 'gap_count': 0, 'gaps': [], 'actions': []},
            },
            root=root / 'runs',
        )
        job = create_research_job(
            root / 'jobs',
            request=f'shared official site learned strategy {index}',
            tags=[
                f'director:{director_id}',
                f"campaign:{director['campaign_id']}",
                'director_followup',
                'director_reason:remediation_upgrade:missing_primary',
                f'remediates_run:{source_run_id}',
                'remediation_gap:missing_primary',
                'remediation_upgrade',
                'remediation_strategy:official_site_search',
            ],
        )
        update_research_job(root / 'jobs', job['job']['job_id'], status='completed', event='completed', run_id=resolved['run_id'])
    persisted = advance_research_director(
        root / 'directors',
        director_id,
        campaign_root=root / 'campaigns',
        jobs_root=root / 'jobs',
        runs_root=root / 'runs',
        synthesis_root=root / 'syntheses',
        apply=True,
        max_followups=0,
    )
    return {'director': director, 'director_id': director_id, 'persisted': persisted, 'source_run_id': source_run_id}


class ResearchDirectorTests(unittest.TestCase):
    def test_director_preview_does_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )

            self.assertFalse((root / 'directors').exists())
            self.assertFalse((root / 'campaigns').exists())
            self.assertFalse((root / 'jobs').exists())

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['planned_director']['initial_step_count'], 6)

    def test_director_apply_creates_campaign_and_queued_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            jobs = list_research_jobs(root / 'jobs', limit=20)
            director_path_exists = Path(result['director']['director_path']).exists()

        self.assertTrue(result['ok'])
        self.assertFalse(result['dry_run'])
        self.assertEqual(result['director']['initial_job_count'], 6)
        self.assertEqual(jobs['count'], 6)
        self.assertTrue(director_path_exists)

    def test_director_creation_discovers_prior_objective_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prior = save_research_run(
                'deep_research',
                'local research agents comparison',
                {
                    'ok': True,
                    'final_report': '# Prior\n\nPrior research about local research agents.',
                    'sources': [
                        {
                            'source_id': 1,
                            'title': 'Agent Docs',
                            'final_url': 'https://docs.example.com/agents',
                        }
                    ],
                    'blocked_sources': [
                        {
                            'url': 'https://blocked.example.com/agents',
                            'block_type': 'captcha',
                        }
                    ],
                    'research_quality': {'label': 'strong', 'score': 82},
                },
                root=root / 'runs',
            )
            preview = research_director_command(
                'objective: Compare local research agents\ndepth=standard',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            created = research_director_command(
                'objective: Compare local research agents\ndepth=standard\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )

        memory = preview['planned_director']['objective_memory']
        self.assertEqual(memory['prior_runs'][0]['run_id'], prior['run_id'])
        self.assertEqual(memory['reusable_sources'][0]['url'], 'https://docs.example.com/agents')
        self.assertEqual(memory['avoid_paths'][0]['url'], 'https://blocked.example.com/agents')
        self.assertEqual(created['director']['objective_memory']['counts']['prior_runs'], 1)
        self.assertEqual(created['director']['objective_memory']['counts']['reusable_sources'], 1)

    def test_director_advance_previews_and_creates_followup_jobs_from_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root, quality_score=42, primary_sources=0)

            preview = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=False,
                max_followups=2,
            )
            applied = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=True,
                max_followups=2,
            )
            leased_followup = lease_next_research_job(root / 'jobs', worker_id='director-test')
            resolved = save_research_run(
                'deep_research',
                'resolved remediation followup',
                {
                    'ok': True,
                    'final_report': '# Resolved\n\nPrimary evidence was added.',
                    'sources': [{'source_id': 1, 'title': 'Official Docs', 'final_url': 'https://docs.example.com/official'}],
                    'claims': [{'claim_id': 1, 'claim': 'Primary evidence was added.', 'supporting_sources': [1]}],
                    'research_quality': {'label': 'strong', 'score': 82},
                    'source_quality': {'label': 'strong', 'score': 80, 'primary_source_count': 1},
                    'remediation_plan': {'ok': True, 'gap_count': 0, 'gaps': [], 'actions': []},
                },
                root=root / 'runs',
            )
            finish_research_job(
                root / 'jobs',
                leased_followup['job']['job_id'],
                lease_id=leased_followup['lease_id'],
                status='completed',
                event='completed',
                run_id=resolved['run_id'],
            )
            post = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=False,
                max_followups=0,
            )
            jobs = list_research_jobs(root / 'jobs', limit=20)

        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['assessment']['quality_gate']['recommended_action'], 'continue')
        self.assertTrue(
            any(
                str(candidate.get('reason')).startswith('evidence_remediation:')
                for candidate in preview['assessment']['follow_up_candidates']
            )
        )
        self.assertIn('preview_followup_jobs', preview['actions'])
        self.assertEqual(len(preview['planned_followups']), 2)
        self.assertFalse(applied['dry_run'])
        self.assertIn('created_followup_jobs', applied['actions'])
        self.assertEqual(sum(1 for job in jobs['jobs'] if 'director_followup' in job['tags']), 2)
        self.assertEqual(post['assessment']['remediation_outcomes']['counts']['resolved'], 1)

    def test_director_upgrades_strategy_when_remediation_gap_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=9\nquality_target=strong\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root, quality_score=42, primary_sources=0)
            first_followup = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=True,
                max_followups=1,
            )
            leased_followup = lease_next_research_job(root / 'jobs', worker_id='director-test')
            still_missing = save_research_run(
                'deep_research',
                'still missing primary evidence',
                {
                    'ok': True,
                    'final_report': '# Still Weak\n\nThe repair still lacks primary evidence.',
                    'sources': [{'source_id': 1, 'title': 'Blog', 'final_url': 'https://example.com/blog'}],
                    'claims': [{'claim_id': 1, 'claim': 'The repair still lacks primary evidence.', 'supporting_sources': [1]}],
                    'research_quality': {'label': 'weak', 'score': 45},
                    'source_quality': {'label': 'limited', 'score': 35, 'primary_source_count': 0},
                    'remediation_plan': {
                        'ok': False,
                        'gap_count': 1,
                        'gaps': [{'code': 'missing_primary', 'message': 'No strong primary source was selected.', 'severity': 'high'}],
                        'actions': [],
                    },
                },
                root=root / 'runs',
            )
            finish_research_job(
                root / 'jobs',
                leased_followup['job']['job_id'],
                lease_id=leased_followup['lease_id'],
                status='completed',
                event='completed',
                run_id=still_missing['run_id'],
            )
            preview = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=False,
                max_followups=1,
            )
            applied = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=True,
                max_followups=1,
            )
            jobs = list_research_jobs(root / 'jobs', limit=30)

        self.assertIn('created_followup_jobs', first_followup['actions'])
        self.assertEqual(preview['assessment']['remediation_outcomes']['counts']['remaining'], 1)
        self.assertEqual(preview['assessment']['remediation_strategy_upgrades'][0]['target_gap'], 'missing_primary')
        self.assertEqual(preview['planned_followups'][0]['reason'], 'remediation_upgrade:missing_primary')
        self.assertIn('primary source only', preview['planned_followups'][0]['query'])
        self.assertIn('created_followup_jobs', applied['actions'])
        self.assertTrue(
            any(
                'director_reason:remediation_upgrade:missing_primary' in job['tags']
                and 'remediation_upgrade' in job['tags']
                for job in jobs['jobs']
            )
        )

    def test_director_learns_successful_remediation_upgrade_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=20\nquality_target=strong\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director = created['director']
            director_id = director['director_id']
            campaign_id = director['campaign_id']
            source_runs = _complete_all_remaining_jobs(root, quality_score=42, primary_sources=0)
            source_run_id = source_runs[0]['run_id']
            for index in range(5):
                resolved = save_research_run(
                    'deep_research',
                    f'learned official site success {index}',
                    {
                        'ok': True,
                        'final_report': '# Resolved\n\nOfficial source resolved the missing primary gap.',
                        'sources': [{'source_id': 1, 'title': 'Official Site', 'final_url': f'https://official.example.com/{index}'}],
                        'claims': [{'claim_id': 1, 'claim': 'Official source resolved the gap.', 'supporting_sources': [1]}],
                        'research_quality': {'label': 'strong', 'score': 84},
                        'source_quality': {'label': 'strong', 'score': 82, 'primary_source_count': 1},
                        'remediation_plan': {'ok': True, 'gap_count': 0, 'gaps': [], 'actions': []},
                    },
                    root=root / 'runs',
                )
                job = create_research_job(
                    root / 'jobs',
                    request=f'official site learned strategy {index}',
                    tags=[
                        f'director:{director_id}',
                        f'campaign:{campaign_id}',
                        'director_followup',
                        'director_reason:remediation_upgrade:missing_primary',
                        f'remediates_run:{source_run_id}',
                        'remediation_gap:missing_primary',
                        'remediation_upgrade',
                        'remediation_strategy:official_site_search',
                    ],
                )
                update_research_job(
                    root / 'jobs',
                    job['job']['job_id'],
                    status='completed',
                    event='completed',
                    run_id=resolved['run_id'],
                )
            unresolved = save_research_run(
                'deep_research',
                'plain remediation still missing primary',
                {
                    'ok': True,
                    'final_report': '# Still Weak\n\nThe plain repair still lacks primary evidence.',
                    'sources': [{'source_id': 1, 'title': 'Article', 'final_url': 'https://example.com/article'}],
                    'claims': [{'claim_id': 1, 'claim': 'The plain repair still lacks primary evidence.', 'supporting_sources': [1]}],
                    'research_quality': {'label': 'weak', 'score': 44},
                    'source_quality': {'label': 'limited', 'score': 34, 'primary_source_count': 0},
                    'remediation_plan': {
                        'ok': False,
                        'gap_count': 1,
                        'gaps': [{'code': 'missing_primary', 'message': 'No strong primary source was selected.', 'severity': 'high'}],
                        'actions': [],
                    },
                },
                root=root / 'runs',
            )
            plain_job = create_research_job(
                root / 'jobs',
                request='plain missing primary remediation',
                tags=[
                    f'director:{director_id}',
                    f'campaign:{campaign_id}',
                    'director_followup',
                    'director_reason:evidence_remediation:missing_primary',
                    f'remediates_run:{source_run_id}',
                    'remediation_gap:missing_primary',
                ],
            )
            update_research_job(
                root / 'jobs',
                plain_job['job']['job_id'],
                status='completed',
                event='completed',
                run_id=unresolved['run_id'],
            )
            preview = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=False,
                max_followups=1,
            )

        learning = preview['assessment']['remediation_strategy_learning']
        official = next(item for item in learning['strategies'] if item['strategy'] == 'official_site_search')
        self.assertEqual(official['resolved'], 5)
        self.assertGreater(official['learned_priority_delta'], 0)
        self.assertEqual(preview['planned_followups'][0]['strategy'], 'official_site_search')
        self.assertGreater(preview['planned_followups'][0]['learned_priority_delta'], 0)
        self.assertIn('site:gov', preview['planned_followups'][0]['query'])

    def test_director_reuses_shared_remediation_strategy_learning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=20\nquality_target=strong\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            first_director = first['director']
            first_director_id = first_director['director_id']
            first_source_run_id = _complete_all_remaining_jobs(root, quality_score=42, primary_sources=0)[0]['run_id']
            for index in range(5):
                resolved = save_research_run(
                    'deep_research',
                    f'shared official site success {index}',
                    {
                        'ok': True,
                        'final_report': '# Resolved\n\nOfficial source resolved the missing primary gap.',
                        'sources': [{'source_id': 1, 'title': 'Official Site', 'final_url': f'https://official.example.com/shared-{index}'}],
                        'claims': [{'claim_id': 1, 'claim': 'Official source resolved the gap.', 'supporting_sources': [1]}],
                        'research_quality': {'label': 'strong', 'score': 84},
                        'source_quality': {'label': 'strong', 'score': 82, 'primary_source_count': 1},
                        'remediation_plan': {'ok': True, 'gap_count': 0, 'gaps': [], 'actions': []},
                    },
                    root=root / 'runs',
                )
                job = create_research_job(
                    root / 'jobs',
                    request=f'shared official site learned strategy {index}',
                    tags=[
                        f'director:{first_director_id}',
                        f"campaign:{first_director['campaign_id']}",
                        'director_followup',
                        'director_reason:remediation_upgrade:missing_primary',
                        f'remediates_run:{first_source_run_id}',
                        'remediation_gap:missing_primary',
                        'remediation_upgrade',
                        'remediation_strategy:official_site_search',
                    ],
                )
                update_research_job(root / 'jobs', job['job']['job_id'], status='completed', event='completed', run_id=resolved['run_id'])
            persisted = advance_research_director(
                root / 'directors',
                first_director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=True,
                max_followups=0,
            )
            second = research_director_command(
                'objective: Build another local deep research system\ndepth=standard\nbudget_jobs=9\nquality_target=strong\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            second_director = second['director']
            second_source_run_id = _complete_all_remaining_jobs(root, quality_score=42, primary_sources=0)[0]['run_id']
            unresolved = save_research_run(
                'deep_research',
                'shared plain remediation still missing primary',
                {
                    'ok': True,
                    'final_report': '# Still Weak\n\nThe plain repair still lacks primary evidence.',
                    'sources': [{'source_id': 1, 'title': 'Article', 'final_url': 'https://example.com/shared-article'}],
                    'claims': [{'claim_id': 1, 'claim': 'The plain repair still lacks primary evidence.', 'supporting_sources': [1]}],
                    'research_quality': {'label': 'weak', 'score': 44},
                    'source_quality': {'label': 'limited', 'score': 34, 'primary_source_count': 0},
                    'remediation_plan': {
                        'ok': False,
                        'gap_count': 1,
                        'gaps': [{'code': 'missing_primary', 'message': 'No strong primary source was selected.', 'severity': 'high'}],
                        'actions': [],
                    },
                },
                root=root / 'runs',
            )
            plain_job = create_research_job(
                root / 'jobs',
                request='second director plain missing primary remediation',
                tags=[
                    f"director:{second_director['director_id']}",
                    f"campaign:{second_director['campaign_id']}",
                    'director_followup',
                    'director_reason:evidence_remediation:missing_primary',
                    f'remediates_run:{second_source_run_id}',
                    'remediation_gap:missing_primary',
                ],
            )
            update_research_job(root / 'jobs', plain_job['job']['job_id'], status='completed', event='completed', run_id=unresolved['run_id'])
            preview = advance_research_director(
                root / 'directors',
                second_director['director_id'],
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=False,
                max_followups=1,
            )
            store_exists = (root / 'directors' / 'remediation_strategy_learning.json').exists()

        self.assertTrue(persisted['shared_remediation_strategy_learning']['ok'])
        self.assertTrue(store_exists)
        shared = preview['assessment']['remediation_strategy_learning']['shared']
        self.assertEqual(shared['director_count'], 1)
        self.assertEqual(preview['planned_followups'][0]['strategy'], 'official_site_search')
        self.assertGreater(preview['planned_followups'][0]['learned_priority_delta'], 0)

    def test_director_quality_gate_synthesizes_when_all_checks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=moderate\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root)
            preview = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=False,
            )
            applied = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=True,
            )
            bundle_exists = Path(applied['synthesis']['bundle_dir']).exists()

        self.assertEqual(preview['assessment']['quality_gate']['recommended_action'], 'synthesize')
        self.assertGreaterEqual(preview['assessment']['quality_gate']['planned_authority_source_count'], 2)
        self.assertGreaterEqual(preview['assessment']['quality_gate']['selected_authority_source_count'], 1)
        self.assertGreaterEqual(preview['assessment']['quality_gate']['planned_low_value_source_count'], 1)
        self.assertIn('preview_synthesis', preview['actions'])
        self.assertIn('wrote_synthesis', applied['actions'])
        self.assertTrue(bundle_exists)

    def test_director_quality_gate_continues_when_answer_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=moderate\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            runs = _complete_all_remaining_jobs(root)
            for saved in runs:
                run_path = Path(saved['run_path'])
                data = json.loads(run_path.read_text(encoding='utf-8'))
                data['payload']['answer_readiness'] = {
                    'ok': False,
                    'label': 'not_ready',
                    'score': 44,
                    'blockers': ['Citation validation failed.'],
                    'warnings': ['Needs more primary evidence.'],
                }
                run_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
            preview = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=False,
                max_followups=2,
            )
            applied = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=True,
                max_followups=1,
            )
            jobs = list_research_jobs(root / 'jobs', limit=20)

        gate = preview['assessment']['quality_gate']
        self.assertEqual(gate['recommended_action'], 'continue')
        self.assertFalse(gate['checks']['answers_ready_to_present'])
        self.assertEqual(gate['not_ready_run_count'], len(runs))
        self.assertIn('preview_followup_jobs', preview['actions'])
        self.assertEqual(preview['planned_followups'][0]['reason'], 'evidence_remediation:answer_not_ready')
        self.assertIn('created_followup_jobs', applied['actions'])
        self.assertTrue(any('director_reason:evidence_remediation:answer_not_ready' in job['tags'] for job in jobs['jobs']))

    def test_director_quality_gate_stops_when_budget_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=6\nquality_target=strong\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root, quality_score=42, primary_sources=0)
            result = advance_research_director(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                apply=True,
            )

        self.assertEqual(result['assessment']['quality_gate']['recommended_action'], 'stop_budget_exhausted')
        self.assertIn('quality_gate_stop_budget_exhausted', result['actions'])
        self.assertEqual(result['director']['status'], 'stopped_budget_exhausted')

    def test_director_wave_preview_observes_gate_without_writing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            result = run_research_director_wave(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
                apply=False,
                start_worker_enabled=True,
                max_cycles=2,
            )

            self.assertFalse((root / 'worker').exists())
            self.assertFalse((root / 'directors' / director_id / 'waves').exists())

        self.assertTrue(result['dry_run'])
        self.assertEqual(result['stop_reason'], 'waiting_for_worker')
        self.assertEqual(result['cycles'][0]['action'], 'wait')
        self.assertTrue(result['worker_start']['dry_run'])

    def test_director_wave_apply_writes_artifact_and_synthesizes_when_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=moderate\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root)
            result = run_research_director_wave(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
                apply=True,
                start_worker_enabled=False,
                max_cycles=2,
            )
            wave_path = Path(result['wave_dir']) / 'wave.json'
            wave_exists = wave_path.exists()
            synthesis_exists = Path(result['cycles'][0]['advance']['synthesis']['bundle_dir']).exists()

        self.assertFalse(result['dry_run'])
        self.assertEqual(result['stop_reason'], 'synthesize')
        self.assertTrue(wave_exists)
        self.assertTrue(synthesis_exists)

    def test_director_autopilot_preview_does_not_write_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            result = run_research_director_autopilot(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
                apply=False,
                start_worker_enabled=True,
                max_iterations=2,
                max_cycles_per_iteration=1,
            )

            self.assertFalse((root / 'worker').exists())
            self.assertFalse((root / 'directors' / director_id / 'autopilots').exists())

        self.assertTrue(result['dry_run'])
        self.assertEqual(result['worker_mode'], 'detached')
        self.assertEqual(result['stop_reason'], 'waiting_for_worker')
        self.assertIn('Autopilot queued or observed work', result['message'])
        self.assertIn('Research Director Autopilot', result['markdown'])

    def test_director_autopilot_apply_writes_ledger_and_synthesizes_when_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=moderate\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root)
            result = research_director_command(
                f'director_id: {director_id}\naction: autopilot\nmax_iterations=3\nmax_cycles=1\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
            )
            autopilot_path = Path(result['autopilot_path'])
            report_path = Path(result['report_path'])
            saved = json.loads(autopilot_path.read_text(encoding='utf-8'))
            director = json.loads((root / 'directors' / director_id / 'director.json').read_text(encoding='utf-8'))
            autopilot_exists = autopilot_path.exists()
            report_exists = report_path.exists()

        self.assertFalse(result['dry_run'])
        self.assertEqual(result['stop_reason'], 'synthesize')
        self.assertTrue(autopilot_exists)
        self.assertTrue(report_exists)
        self.assertEqual(saved['stop_reason'], 'synthesize')
        self.assertTrue(any(event.get('event') == 'autopilot' for event in director['events']))
        self.assertIn('dashboard', result['markdown'])
        self.assertIn('runbook', result['markdown'])

    def test_director_autopilot_stops_after_queuing_followups_when_worker_is_external(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=strong\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root, quality_score=42, primary_sources=0)
            result = research_director_command(
                f'director_id: {director_id}\naction: autopilot\nmax_iterations=3\nmax_cycles=1\nmax_followups=2\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
            )
            jobs = list_research_jobs(root / 'jobs', limit=20)
            followup_jobs = [job for job in jobs['jobs'] if 'director_followup' in job.get('tags', [])]

        self.assertEqual(result['stop_reason'], 'waiting_for_worker')
        self.assertEqual(len(followup_jobs), 2)
        self.assertEqual(result['iterations'][0]['wave']['cycles'][0]['action'], 'continue')

    def test_director_dashboard_collects_gate_waves_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=moderate\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root)
            run_research_director_wave(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
                apply=True,
                max_cycles=1,
            )
            preview = build_research_director_dashboard(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                apply=False,
            )
            written = build_research_director_dashboard(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                apply=True,
            )
            report_exists = Path(written['report_path']).exists()
            dashboard_exists = Path(written['dashboard_path']).exists()
            markdown = Path(written['report_path']).read_text(encoding='utf-8')

        self.assertTrue(preview['ok'])
        self.assertTrue(preview['dry_run'])
        self.assertGreaterEqual(preview['wave_count'], 1)
        self.assertIn('Current gate action', preview['markdown'])
        self.assertTrue(report_exists)
        self.assertTrue(dashboard_exists)
        self.assertIn('## Wave History', markdown)
        self.assertIn('## Synthesis', markdown)
        self.assertIn('## Graph Insights', markdown)
        self.assertIn('central_claims', preview['graph_summary'])
        self.assertGreaterEqual(len(preview['graph_summary']['central_claims']), 1)
        self.assertGreaterEqual(len(preview['graph_summary']['repeated_source_domains']), 1)
        self.assertGreaterEqual(len(preview['graph_summary']['next_best_graph_actions']), 1)

    def test_director_evidence_graph_links_steps_runs_sources_claims_and_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=moderate\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root)
            run_research_director_wave(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
                apply=True,
                max_cycles=1,
            )
            preview = build_director_evidence_graph(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
            )
            written = research_director_command(
                f'director_id: {director_id}\naction: graph\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            graph_path = Path(written['graph_path'])
            index_path = Path(written['index_path'])
            saved_graph = json.loads(graph_path.read_text(encoding='utf-8'))
            graph_exists = graph_path.exists()
            index_exists = index_path.exists()

        relations = {edge['relation'] for edge in preview['edges']}
        kinds = {node['kind'] for node in preview['nodes']}
        self.assertTrue(preview['dry_run'])
        self.assertIn('campaign_step', kinds)
        self.assertIn('run', kinds)
        self.assertIn('source', kinds)
        self.assertIn('claim', kinds)
        self.assertIn('synthesis', kinds)
        self.assertIn('produced_run', relations)
        self.assertIn('read_source', relations)
        self.assertIn('supported_by', relations)
        self.assertTrue(graph_exists)
        self.assertTrue(index_exists)
        self.assertEqual(saved_graph['counts']['nodes'], written['counts']['nodes'])

    def test_director_graph_actions_preview_and_queue_targeted_followups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=strong\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_one_graph_issue_job(root)
            preview = execute_director_graph_actions(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                selected_action='weak_claims',
                max_actions=2,
            )
            applied = research_director_command(
                f'director_id: {director_id}\naction: graph_actions\ngraph_action=weak_claims\nmax_actions=2\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            jobs = list_research_jobs(root / 'jobs', limit=20)
            graph_jobs = [job for job in jobs['jobs'] if 'director_graph_action' in (job.get('tags') or [])]

        self.assertTrue(preview['dry_run'])
        self.assertEqual(len(preview['planned_jobs']), 2)
        self.assertEqual(preview['planned_jobs'][0]['graph_action'], 'create_followup_for_weak_claims')
        self.assertFalse(applied['dry_run'])
        self.assertEqual(len(applied['created_jobs']), 2)
        self.assertEqual(len(graph_jobs), 2)
        self.assertTrue(all('graph_action:create_followup_for_weak_claims' in (job.get('tags') or []) for job in graph_jobs))

    def test_director_runbook_previews_and_writes_operator_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=moderate\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root)
            run_research_director_wave(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
                apply=True,
                max_cycles=1,
            )
            preview = build_director_runbook(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
            )
            written = research_director_command(
                f'director_id: {director_id}\naction: runbook\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            runbook_path = Path(written['runbook_path'])
            report_path = Path(written['report_path'])
            saved = json.loads(runbook_path.read_text(encoding='utf-8'))
            markdown = report_path.read_text(encoding='utf-8')
            runbook_exists = runbook_path.exists()
            report_exists = report_path.exists()

        self.assertTrue(preview['dry_run'])
        self.assertIn('dashboard', preview)
        self.assertIn('evidence_graph', preview)
        self.assertIn('recovery', preview)
        self.assertIn('commands', preview)
        self.assertGreaterEqual(len(preview['commands']), 3)
        self.assertTrue(runbook_exists)
        self.assertTrue(report_exists)
        self.assertIn('Exact Next Commands', markdown)
        self.assertEqual(saved['director']['director_id'], director_id)

    def test_director_runbook_export_profiles_write_redacted_checksummed_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\nquality_target=moderate\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            _complete_all_remaining_jobs(root)
            preview = export_director_runbook(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                profile='private-share',
            )
            exported = research_director_command(
                f'director_id: {director_id}\naction: runbook_export\nprofile=private-share\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            manifest = json.loads(Path(exported['manifest_path']).read_text(encoding='utf-8'))
            manifest_text = Path(exported['manifest_path']).read_text(encoding='utf-8')
            manifest_hash = hashlib.sha256(Path(exported['manifest_path']).read_bytes()).hexdigest()
            export_dir = Path(exported['export_dir'])
            graph_text = (export_dir / 'evidence_graph' / 'evidence_graph.json').read_text(encoding='utf-8')
            archive_path = Path(exported['archive']['path'])
            archive_exists = archive_path.exists()
            exported_text = '\n'.join(path.read_text(encoding='utf-8') for path in export_dir.rglob('*') if path.is_file() and path.suffix in {'.json', '.md'})
            leaked_roots = [str(root), str(root.resolve())]
            with tarfile.open(archive_path, 'r:gz') as archive:
                tar_members = archive.getmembers()

        self.assertTrue(preview['dry_run'])
        self.assertTrue(preview['profile']['redact'])
        self.assertIn('manifest.json', preview['planned_files'])
        self.assertFalse(exported['dry_run'])
        self.assertTrue(exported['profile']['redact'])
        self.assertTrue(archive_exists)
        self.assertGreaterEqual(exported['file_count'], 6)
        self.assertTrue(all(item.get('sha256') for item in exported['files']))
        self.assertEqual(exported['files'], manifest['files'])
        self.assertEqual(exported['file_count'], manifest['file_count'])
        self.assertEqual(exported['manifest_file']['sha256'], manifest_hash)
        self.assertNotIn(str(root), manifest_text)
        self.assertTrue(all(leak not in exported_text for leak in leaked_roots))
        self.assertFalse(any('path' in item or 'source_path' in item for item in manifest['files']))
        self.assertNotIn('path', exported['manifest_file'])
        self.assertNotIn('source_path', exported['manifest_file'])
        self.assertFalse(any(item.get('relative_path') == 'manifest.json' for item in manifest['files']))
        self.assertEqual(manifest['profile']['name'], 'private-share')
        self.assertTrue(tar_members)
        self.assertTrue(all(member.uid == 0 and member.gid == 0 and member.uname == '' and member.gname == '' and member.mtime == 0 for member in tar_members))
        self.assertIn('[redacted-url]', graph_text)
        self.assertNotIn('https://example.com/docs', graph_text)

    def test_director_runbook_exports_remediation_learning_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeded = _seed_shared_official_site_learning(root)
            director_id = seeded['director_id']
            runbook = build_director_runbook(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                apply=True,
            )
            exported = export_director_runbook(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                apply=True,
                profile='full-fidelity',
                archive=False,
            )
            snapshot_path = Path(runbook['remediation_learning_path'])
            exported_snapshot_path = Path(exported['export_dir']) / 'remediation_learning' / 'remediation_learning.json'
            exported_store_path = Path(exported['export_dir']) / 'remediation_learning' / 'remediation_strategy_learning.json'
            snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
            exported_snapshot = json.loads(exported_snapshot_path.read_text(encoding='utf-8'))
            exported_store = json.loads(exported_store_path.read_text(encoding='utf-8'))
            relative_paths = {item['relative_path'] for item in exported['files']}
            snapshot_exists = snapshot_path.exists()
            exported_snapshot_exists = exported_snapshot_path.exists()
            exported_store_exists = exported_store_path.exists()

        self.assertTrue(seeded['persisted']['shared_remediation_strategy_learning']['ok'])
        self.assertTrue(snapshot_exists)
        self.assertTrue(exported_snapshot_exists)
        self.assertTrue(exported_store_exists)
        self.assertIn('Remediation Learning Export', runbook['markdown'])
        self.assertIn('remediation_learning/remediation_learning.json', relative_paths)
        self.assertIn('remediation_learning/remediation_strategy_learning.json', relative_paths)
        self.assertTrue(any(item['strategy'] == 'official_site_search' for item in snapshot['top_shared_strategies']))
        self.assertEqual(exported_snapshot['strategy_count'], snapshot['strategy_count'])
        self.assertEqual(exported_store['director_count'], 1)

    def test_director_imports_remediation_learning_export_into_fresh_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            target_root = Path(tmp) / 'target'
            seeded = _seed_shared_official_site_learning(source_root)
            exported = export_director_runbook(
                source_root / 'directors',
                seeded['director_id'],
                campaign_root=source_root / 'campaigns',
                jobs_root=source_root / 'jobs',
                runs_root=source_root / 'runs',
                apply=True,
                profile='full-fidelity',
                archive=False,
            )
            preview = research_director_command(
                f'action: import_learning\nsource={exported["export_dir"]}',
                root=target_root / 'directors',
                campaign_root=target_root / 'campaigns',
                jobs_root=target_root / 'jobs',
                runs_root=target_root / 'runs',
                synthesis_root=target_root / 'syntheses',
            )
            imported = research_director_command(
                f'action: import_learning\nsource={exported["export_dir"]}\napply=true',
                root=target_root / 'directors',
                campaign_root=target_root / 'campaigns',
                jobs_root=target_root / 'jobs',
                runs_root=target_root / 'runs',
                synthesis_root=target_root / 'syntheses',
            )
            created = research_director_command(
                'objective: Build a fresh local deep research system\ndepth=standard\nbudget_jobs=9\nquality_target=strong\napply=true',
                root=target_root / 'directors',
                campaign_root=target_root / 'campaigns',
                jobs_root=target_root / 'jobs',
                runs_root=target_root / 'runs',
                synthesis_root=target_root / 'syntheses',
            )
            director = created['director']
            source_run_id = _complete_all_remaining_jobs(target_root, quality_score=42, primary_sources=0)[0]['run_id']
            unresolved = save_research_run(
                'deep_research',
                'imported learning plain remediation still missing primary',
                {
                    'ok': True,
                    'final_report': '# Still Weak\n\nThe plain repair still lacks primary evidence.',
                    'sources': [{'source_id': 1, 'title': 'Article', 'final_url': 'https://example.com/imported-article'}],
                    'claims': [{'claim_id': 1, 'claim': 'The plain repair still lacks primary evidence.', 'supporting_sources': [1]}],
                    'research_quality': {'label': 'weak', 'score': 44},
                    'source_quality': {'label': 'limited', 'score': 34, 'primary_source_count': 0},
                    'remediation_plan': {
                        'ok': False,
                        'gap_count': 1,
                        'gaps': [{'code': 'missing_primary', 'message': 'No strong primary source was selected.', 'severity': 'high'}],
                        'actions': [],
                    },
                },
                root=target_root / 'runs',
            )
            plain_job = create_research_job(
                target_root / 'jobs',
                request='fresh director plain missing primary remediation',
                tags=[
                    f"director:{director['director_id']}",
                    f"campaign:{director['campaign_id']}",
                    'director_followup',
                    'director_reason:evidence_remediation:missing_primary',
                    f'remediates_run:{source_run_id}',
                    'remediation_gap:missing_primary',
                ],
            )
            update_research_job(target_root / 'jobs', plain_job['job']['job_id'], status='completed', event='completed', run_id=unresolved['run_id'])
            followup_preview = advance_research_director(
                target_root / 'directors',
                director['director_id'],
                campaign_root=target_root / 'campaigns',
                jobs_root=target_root / 'jobs',
                runs_root=target_root / 'runs',
                synthesis_root=target_root / 'syntheses',
                apply=False,
                max_followups=1,
            )
            imported_store_exists = (target_root / 'directors' / 'remediation_strategy_learning.json').exists()

        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['imported_director_count'], 1)
        self.assertTrue(imported['ok'])
        self.assertFalse(imported['dry_run'])
        self.assertTrue(imported_store_exists)
        self.assertEqual(followup_preview['planned_followups'][0]['strategy'], 'official_site_search')
        self.assertGreater(followup_preview['planned_followups'][0]['learned_priority_delta'], 0)

    def test_director_imports_remediation_learning_from_runbook_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            target_root = Path(tmp) / 'target'
            seeded = _seed_shared_official_site_learning(source_root)
            exported = export_director_runbook(
                source_root / 'directors',
                seeded['director_id'],
                campaign_root=source_root / 'campaigns',
                jobs_root=source_root / 'jobs',
                runs_root=source_root / 'runs',
                apply=True,
                profile='full-fidelity',
                archive=True,
            )
            imported = research_director_command(
                f'action: restore_learning\nsource={exported["archive"]["path"]}\napply=true',
                root=target_root / 'directors',
                campaign_root=target_root / 'campaigns',
                jobs_root=target_root / 'jobs',
                runs_root=target_root / 'runs',
                synthesis_root=target_root / 'syntheses',
            )
            imported_store = json.loads((target_root / 'directors' / 'remediation_strategy_learning.json').read_text(encoding='utf-8'))

        self.assertTrue(imported['ok'])
        self.assertEqual(imported['imported_director_count'], 1)
        self.assertEqual(imported_store['director_count'], 1)
        self.assertTrue(any(item['strategy'] == 'official_site_search' for item in imported_store['aggregate']['strategies']))

    def test_director_imports_remediation_learning_from_runbook_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            target_root = Path(tmp) / 'target'
            seeded = _seed_shared_official_site_learning(source_root)
            runbook = build_director_runbook(
                source_root / 'directors',
                seeded['director_id'],
                campaign_root=source_root / 'campaigns',
                jobs_root=source_root / 'jobs',
                runs_root=source_root / 'runs',
                apply=True,
            )
            imported = research_director_command(
                f'action: import_learning\nsource={runbook["runbook_path"]}\napply=true',
                root=target_root / 'directors',
                campaign_root=target_root / 'campaigns',
                jobs_root=target_root / 'jobs',
                runs_root=target_root / 'runs',
                synthesis_root=target_root / 'syntheses',
            )
            imported_store = json.loads((target_root / 'directors' / 'remediation_strategy_learning.json').read_text(encoding='utf-8'))

        self.assertTrue(imported['ok'])
        self.assertEqual(imported['imported_director_count'], 1)
        self.assertTrue(any(item['strategy'] == 'official_site_search' for item in imported_store['aggregate']['strategies']))

    def test_director_imports_remediation_learning_from_snapshot_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            target_root = Path(tmp) / 'target'
            seeded = _seed_shared_official_site_learning(source_root)
            runbook = build_director_runbook(
                source_root / 'directors',
                seeded['director_id'],
                campaign_root=source_root / 'campaigns',
                jobs_root=source_root / 'jobs',
                runs_root=source_root / 'runs',
                apply=True,
            )
            imported = research_director_command(
                f'action: restore_learning\nsource={runbook["remediation_learning_path"]}\napply=true',
                root=target_root / 'directors',
                campaign_root=target_root / 'campaigns',
                jobs_root=target_root / 'jobs',
                runs_root=target_root / 'runs',
                synthesis_root=target_root / 'syntheses',
            )
            imported_store = json.loads((target_root / 'directors' / 'remediation_strategy_learning.json').read_text(encoding='utf-8'))

        self.assertTrue(imported['ok'])
        self.assertEqual(imported['imported_director_count'], 1)
        self.assertTrue(any(item['strategy'] == 'official_site_search' for item in imported_store['aggregate']['strategies']))

    def test_director_bundle_comparison_diffs_claims_sources_and_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            left_dir = root / 'left'
            right_dir = root / 'right'
            left_graph = {
                'ok': True,
                'nodes': [
                    {'id': 'claim:1', 'kind': 'claim', 'label': 'Old claim'},
                    {'id': 'source:1', 'kind': 'source', 'label': 'Old source', 'url': 'https://old.example/a', 'domain': 'old.example'},
                    {'id': 'contradiction:1', 'kind': 'contradiction', 'label': 'Old contradiction', 'status': 'unresolved'},
                ],
                'edges': [
                    {'source': 'claim:1', 'target': 'source:1', 'relation': 'supported_by'},
                    {'source': 'run:1', 'target': 'contradiction:1', 'relation': 'has_contradiction'},
                ],
            }
            right_graph = {
                'ok': True,
                'nodes': [
                    {'id': 'claim:1', 'kind': 'claim', 'label': 'Old claim'},
                    {'id': 'claim:2', 'kind': 'claim', 'label': 'New weak claim'},
                    {'id': 'source:2', 'kind': 'source', 'label': 'New source', 'url': 'https://new.example/b', 'domain': 'new.example'},
                    {'id': 'contradiction:2', 'kind': 'contradiction', 'label': 'New contradiction', 'status': 'unresolved'},
                ],
                'edges': [
                    {'source': 'claim:1', 'target': 'source:2', 'relation': 'supported_by'},
                    {'source': 'run:2', 'target': 'contradiction:2', 'relation': 'has_contradiction'},
                ],
            }
            (left_dir / 'evidence_graph').mkdir(parents=True)
            (right_dir / 'evidence_graph').mkdir(parents=True)
            (left_dir / 'evidence_graph' / 'evidence_graph.json').write_text(json.dumps(left_graph), encoding='utf-8')
            (right_dir / 'evidence_graph' / 'evidence_graph.json').write_text(json.dumps(right_graph), encoding='utf-8')
            preview = compare_director_bundles(root / 'directors', director_id, left=left_dir, right=right_dir)
            written = research_director_command(
                f'director_id: {director_id}\naction: compare_bundles\nleft={left_dir}\nright={right_dir}\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            comparison_exists = Path(written['comparison_path']).exists()
            report_exists = Path(written['report_path']).exists()

        self.assertTrue(preview['dry_run'])
        self.assertIn('New weak claim', preview['new_claims'])
        self.assertIn('Old contradiction', preview['resolved_contradictions'])
        self.assertEqual(preview['counts']['new_sources'], 1)
        self.assertEqual(preview['counts']['removed_sources'], 1)
        self.assertGreaterEqual(preview['counts']['remaining_gaps'], 1)
        self.assertTrue(comparison_exists)
        self.assertTrue(report_exists)

    def test_director_comparison_actions_preview_and_queue_followups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=12\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            left_dir = root / 'left'
            right_dir = root / 'right'
            left_graph = {
                'ok': True,
                'nodes': [
                    {'id': 'claim:1', 'kind': 'claim', 'label': 'Old claim'},
                    {'id': 'source:1', 'kind': 'source', 'label': 'Old source', 'url': 'https://old.example/a', 'domain': 'old.example'},
                ],
                'edges': [{'source': 'claim:1', 'target': 'source:1', 'relation': 'supported_by'}],
            }
            right_graph = {
                'ok': True,
                'nodes': [
                    {'id': 'claim:2', 'kind': 'claim', 'label': 'New weak claim'},
                    {'id': 'contradiction:2', 'kind': 'contradiction', 'label': 'New contradiction', 'status': 'unresolved'},
                ],
                'edges': [{'source': 'run:2', 'target': 'contradiction:2', 'relation': 'has_contradiction'}],
            }
            (left_dir / 'evidence_graph').mkdir(parents=True)
            (right_dir / 'evidence_graph').mkdir(parents=True)
            (left_dir / 'evidence_graph' / 'evidence_graph.json').write_text(json.dumps(left_graph), encoding='utf-8')
            (right_dir / 'evidence_graph' / 'evidence_graph.json').write_text(json.dumps(right_graph), encoding='utf-8')
            preview = research_director_command(
                f'director_id: {director_id}\naction: comparison_actions\nleft={left_dir}\nright={right_dir}\nmax_actions=3',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            applied = research_director_command(
                f'director_id: {director_id}\naction: comparison_actions\nleft={left_dir}\nright={right_dir}\nmax_actions=3\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            jobs = list_research_jobs(root / 'jobs', limit=20)['jobs']
            comparison_jobs = [job for job in jobs if 'director_comparison_action' in job.get('tags', [])]
            comparison_run = save_research_run(
                'deep_research',
                'comparison followup',
                {
                    'ok': True,
                    'final_report': '# Comparison\n\nFound restored evidence.',
                    'sources': [{'source_id': 1, 'title': 'Restored source', 'final_url': 'https://restored.example/a'}],
                    'claims': [{'claim_id': 1, 'claim': 'Restored evidence supports the claim.', 'supporting_sources': [1]}],
                    'source_quality': {'label': 'strong', 'score': 82, 'primary_source_count': 1},
                    'contradiction_table': {'rows': []},
                },
                root=root / 'runs',
            )
            update_research_job(root / 'jobs', comparison_jobs[0]['job_id'], status='completed', event='completed', run_id=comparison_run['run_id'])
            update_research_job(root / 'jobs', comparison_jobs[1]['job_id'], status='failed', event='failed', message='blocked source')
            dashboard = build_research_director_dashboard(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
            )
            event_id = dashboard['comparison_actions'][0]['event_id']
            replay_preview = research_director_command(
                f'director_id: {director_id}\naction: comparison_replay\nevent_id={event_id}\nmax_actions=2',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            replay_apply = research_director_command(
                f'director_id: {director_id}\naction: comparison_replay\nevent_id={event_id}\nmax_actions=2\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            replay_second_preview = research_director_command(
                f'director_id: {director_id}\naction: comparison_replay\nevent_id={event_id}\nmax_actions=2',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            dashboard_after_replay = build_research_director_dashboard(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
            )
            replay_jobs = list_research_jobs(root / 'jobs', limit=40)['jobs']
            replay_jobs = [job for job in replay_jobs if 'director_comparison_replay' in job.get('tags', [])]
            for job in replay_jobs:
                update_research_job(root / 'jobs', job['job_id'], status='failed', event='failed', message='replay still blocked')
            dashboard_after_failed_replay = build_research_director_dashboard(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
            )
            runbook = build_director_runbook(
                root / 'directors',
                director_id,
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
            )

        self.assertTrue(preview['dry_run'])
        self.assertEqual(len(preview['planned_jobs']), 3)
        self.assertEqual({job['comparison_action'] for job in preview['planned_jobs']}, {'investigate_new_gaps', 'resolve_new_contradictions', 'recover_lost_source_coverage'})
        self.assertFalse(applied['dry_run'])
        self.assertEqual(len(applied['created_jobs']), 3)
        self.assertEqual(len(comparison_jobs), 3)
        self.assertTrue(any('comparison_action:recover_lost_source_coverage' in job.get('tags', []) for job in comparison_jobs))
        self.assertEqual(dashboard['comparison_actions'][0]['created_job_count'], 3)
        self.assertEqual(dashboard['comparison_actions'][0]['job_status_counts'].get('completed'), 1)
        self.assertEqual(dashboard['comparison_actions'][0]['job_status_counts'].get('failed'), 1)
        self.assertEqual(dashboard['comparison_actions'][0]['job_status_counts'].get('queued'), 1)
        self.assertEqual(dashboard['comparison_actions'][0]['impact']['impact_label'], 'evidence_added')
        self.assertEqual(dashboard['comparison_actions'][0]['impact']['sources_found'], 1)
        self.assertEqual(dashboard['comparison_actions'][0]['impact']['claims_found'], 1)
        self.assertEqual(dashboard['comparison_actions'][0]['impact']['primary_source_runs'], 1)
        self.assertTrue(any(item['action'] == 'retry_failed_comparison_followups' for item in dashboard['comparison_actions'][0]['recommendations']))
        self.assertIn('Comparison Actions', dashboard['markdown'])
        self.assertIn('completed:1', dashboard['markdown'])
        self.assertIn('evidence_added', dashboard['markdown'])
        self.assertIn('retry_failed_comparison_followups', dashboard['markdown'])
        self.assertIn('Comparison Action History', runbook['markdown'])
        self.assertTrue(replay_preview['dry_run'])
        self.assertEqual(len(replay_preview['planned_jobs']), 2)
        self.assertIn('REPLAY comparison follow-up', replay_preview['planned_jobs'][0]['request'])
        self.assertFalse(replay_apply['dry_run'])
        self.assertEqual(len(replay_apply['created_jobs']), 2)
        self.assertEqual(len(replay_second_preview['planned_jobs']), 1)
        self.assertEqual(len(replay_jobs), 2)
        self.assertTrue(all(f'replay_of_comparison_event:{event_id}' in job.get('tags', []) for job in replay_jobs))
        self.assertTrue(all(any(tag.startswith('replay_target:') for tag in job.get('tags', [])) for job in replay_jobs))
        original_event_after_replay = next(item for item in dashboard_after_replay['comparison_actions'] if item['event_id'] == event_id)
        self.assertEqual(original_event_after_replay['replay_summary']['replay_job_count'], 2)
        self.assertEqual(original_event_after_replay['replay_summary']['replayed_target_count'], 2)
        self.assertEqual(original_event_after_replay['replay_summary']['next_replay_duplicate_skip_count'], 2)
        self.assertEqual(original_event_after_replay['replay_summary']['job_status_counts'].get('queued'), 2)
        self.assertEqual(len(original_event_after_replay['replay_summary']['replay_event_ids']), 1)
        self.assertTrue(any(item['action'] == 'wait_for_replayed_comparison_followups' for item in original_event_after_replay['recommendations']))
        original_event_after_failed_replay = next(item for item in dashboard_after_failed_replay['comparison_actions'] if item['event_id'] == event_id)
        self.assertEqual(original_event_after_failed_replay['replay_summary']['job_status_counts'].get('failed'), 2)
        self.assertTrue(any(item['action'] == 'change_strategy_for_replayed_comparison_followups' for item in original_event_after_failed_replay['recommendations']))
        self.assertIn('replay jobs=2', dashboard_after_replay['markdown'])
        self.assertIn('skip=2', dashboard_after_replay['markdown'])
        self.assertIn('replay of', dashboard_after_replay['markdown'])
        self.assertIn('replay jobs=2', runbook['markdown'])
        self.assertIn('change_strategy_for_replayed_comparison_followups', runbook['markdown'])

    def test_director_bundle_comparison_keeps_redacted_sources_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            left_dir = root / 'left'
            right_dir = root / 'right'
            left_graph = {
                'ok': True,
                'nodes': [
                    {'id': 'source:left-a', 'kind': 'source', 'label': 'Source A', 'url': '[redacted-url]', 'domain': 'example.com'},
                ],
                'edges': [],
            }
            right_graph = {
                'ok': True,
                'nodes': [
                    {'id': 'source:right-a', 'kind': 'source', 'label': 'Source A', 'url': '[redacted-url]', 'domain': 'example.com'},
                    {'id': 'source:right-b', 'kind': 'source', 'label': 'Source B', 'url': '[redacted-url]', 'domain': 'example.com'},
                ],
                'edges': [],
            }
            (left_dir / 'evidence_graph').mkdir(parents=True)
            (right_dir / 'evidence_graph').mkdir(parents=True)
            (left_dir / 'evidence_graph' / 'evidence_graph.json').write_text(json.dumps(left_graph), encoding='utf-8')
            (right_dir / 'evidence_graph' / 'evidence_graph.json').write_text(json.dumps(right_graph), encoding='utf-8')
            comparison = compare_director_bundles(root / 'directors', director_id, left=left_dir, right=right_dir)

        self.assertEqual(comparison['counts']['new_sources'], 2)
        self.assertEqual(comparison['counts']['removed_sources'], 1)

    def test_director_bundle_comparison_prefers_local_bundle_graph_over_stale_manifest_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            left_dir = root / 'left'
            right_dir = root / 'moved_bundle'
            stale_dir = root / 'original_bundle'
            for bundle in (left_dir, right_dir, stale_dir):
                (bundle / 'evidence_graph').mkdir(parents=True)
            (left_dir / 'evidence_graph' / 'evidence_graph.json').write_text(json.dumps({'ok': True, 'nodes': [], 'edges': []}), encoding='utf-8')
            (right_dir / 'evidence_graph' / 'evidence_graph.json').write_text(
                json.dumps({'ok': True, 'nodes': [{'id': 'claim:local', 'kind': 'claim', 'label': 'Local moved claim'}], 'edges': []}),
                encoding='utf-8',
            )
            (stale_dir / 'evidence_graph' / 'evidence_graph.json').write_text(
                json.dumps({'ok': True, 'nodes': [{'id': 'claim:stale', 'kind': 'claim', 'label': 'Stale original claim'}], 'edges': []}),
                encoding='utf-8',
            )
            (right_dir / 'manifest.json').write_text(json.dumps({'ok': True, 'export_dir': str(stale_dir)}), encoding='utf-8')
            comparison = compare_director_bundles(root / 'directors', director_id, left=left_dir, right=right_dir)

        self.assertIn('Local moved claim', comparison['new_claims'])
        self.assertNotIn('Stale original claim', comparison['new_claims'])
        self.assertEqual(Path(comparison['right_graph_path']).parent.parent, right_dir)

    def test_director_bundle_comparison_does_not_read_cwd_graph_for_manifest_without_export_dir(self) -> None:
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            left_dir = root / 'left'
            right_dir = root / 'manifest_only_bundle'
            cwd_dir = root / 'cwd'
            (left_dir / 'evidence_graph').mkdir(parents=True)
            right_dir.mkdir()
            (cwd_dir / 'evidence_graph').mkdir(parents=True)
            (left_dir / 'evidence_graph' / 'evidence_graph.json').write_text(json.dumps({'ok': True, 'nodes': [], 'edges': []}), encoding='utf-8')
            (right_dir / 'manifest.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
            (cwd_dir / 'evidence_graph' / 'evidence_graph.json').write_text(
                json.dumps({'ok': True, 'nodes': [{'id': 'claim:cwd', 'kind': 'claim', 'label': 'CWD claim'}], 'edges': []}),
                encoding='utf-8',
            )
            try:
                os.chdir(cwd_dir)
                comparison = compare_director_bundles(root / 'directors', director_id, left=left_dir, right=right_dir)
            finally:
                os.chdir(original_cwd)

        self.assertFalse(comparison['ok'])
        self.assertIn('Could not find evidence graph', comparison['message'])

    def test_director_recovery_previews_and_applies_wave_review_and_job_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            wave_dir = root / 'directors' / director_id / 'waves' / 'old-wave'
            wave_dir.mkdir(parents=True)
            wave_path = wave_dir / 'wave.json'
            wave_path.write_text(
                json.dumps(
                    {
                        'ok': True,
                        'wave_id': 'old-wave',
                        'director_id': director_id,
                        'stop_reason': 'max_cycles',
                        'created_at': '2026-01-01T00:00:00Z',
                        'worker_start': {'ok': False, 'message': 'boom'},
                        'cycles': [],
                    }
                ),
                encoding='utf-8',
            )
            stuck = create_research_job(
                root / 'jobs',
                request='stuck director followup',
                tags=[f'director:{director_id}', 'director_followup'],
            )
            for index in range(105):
                create_research_job(root / 'jobs', request=f'irrelevant queued job {index}', priority=100)
            job_path = Path(stuck['job_path'])
            payload = json.loads(job_path.read_text(encoding='utf-8'))
            payload['updated_at'] = '2026-01-01T00:00:00Z'
            job_path.write_text(json.dumps(payload), encoding='utf-8')

            preview = research_director_command(
                f'director_id: {director_id}\naction: recovery\nstale_hours=1',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            applied = research_director_command(
                f'director_id: {director_id}\naction: recovery\nstale_hours=1\ncancel_stuck_jobs=true\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            reviewed_wave = json.loads(wave_path.read_text(encoding='utf-8'))
            loaded_job = load_research_job(root / 'jobs', stuck['job']['job_id'])

        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['issue_counts']['stale_waves'], 1)
        self.assertEqual(preview['issue_counts']['failed_worker_waves'], 1)
        self.assertEqual(preview['issue_counts']['stuck_jobs'], 1)
        self.assertFalse(applied['dry_run'])
        self.assertTrue(reviewed_wave['recovery_reviewed'])
        self.assertEqual(applied['issue_counts']['cancelled_jobs'], 1)
        self.assertEqual(loaded_job['job']['status'], 'cancelled')

    def test_director_recovery_policy_presets_gate_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = research_director_command(
                'objective: Build a local deep research system\ndepth=standard\nbudget_jobs=8\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            director_id = created['director']['director_id']
            stuck = create_research_job(root / 'jobs', request='stuck director followup', tags=[f'director:{director_id}'])
            job_path = Path(stuck['job_path'])
            payload = json.loads(job_path.read_text(encoding='utf-8'))
            payload['updated_at'] = '2026-01-01T00:00:00Z'
            job_path.write_text(json.dumps(payload), encoding='utf-8')
            interrupted = save_research_run(
                'deep_research',
                'interrupted checkpoint',
                {
                    'ok': False,
                    'question': 'interrupted checkpoint',
                    'checkpoint': {'completed_queries': [], 'remaining_queries': ['source query']},
                },
                status='interrupted',
                root=root / 'runs',
            )

            conservative = research_director_command(
                f'director_id: {director_id}\naction: recovery\npolicy=conservative\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
            )
            aggressive_preview = research_director_command(
                f'director_id: {director_id}\naction: recovery\npolicy=aggressive',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
            )
            aggressive_apply = research_director_command(
                f'director_id: {director_id}\naction: recovery\npolicy=aggressive\nstart_worker=false\napply=true',
                root=root / 'directors',
                campaign_root=root / 'campaigns',
                jobs_root=root / 'jobs',
                runs_root=root / 'runs',
                synthesis_root=root / 'syntheses',
                worker_state_dir=root / 'worker',
            )
            loaded_job = load_research_job(root / 'jobs', stuck['job']['job_id'])

        self.assertEqual(conservative['policy']['name'], 'conservative')
        self.assertFalse(conservative['policy']['cancel_stuck_jobs'])
        self.assertEqual(conservative['issue_counts']['cancelled_jobs'], 0)
        self.assertEqual(aggressive_preview['policy']['name'], 'aggressive')
        self.assertEqual(aggressive_preview['stale_hours'], 2)
        self.assertEqual(aggressive_preview['checkpoint_recovery']['resume_actions'][0]['run_id'], interrupted['run_id'])
        self.assertTrue(aggressive_preview['worker_recovery']['start']['dry_run'])
        self.assertEqual(aggressive_apply['issue_counts']['cancelled_jobs'], 1)
        self.assertEqual(loaded_job['job']['status'], 'cancelled')

    def test_director_cli_status_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            applied = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts' / 'research_director.py'),
                    'objective: CLI director\ndepth=standard\nbudget_jobs=7',
                    '--root',
                    str(root / 'directors'),
                    '--campaign-root',
                    str(root / 'campaigns'),
                    '--jobs-root',
                    str(root / 'jobs'),
                    '--runs-root',
                    str(root / 'runs'),
                    '--synthesis-root',
                    str(root / 'syntheses'),
                    '--apply',
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            status = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / 'scripts' / 'research_director.py'),
                    'status',
                    '--root',
                    str(root / 'directors'),
                    '--campaign-root',
                    str(root / 'campaigns'),
                    '--jobs-root',
                    str(root / 'jobs'),
                    '--runs-root',
                    str(root / 'runs'),
                    '--synthesis-root',
                    str(root / 'syntheses'),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertTrue(json.loads(applied.stdout)['ok'])
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)['count'], 1)


if __name__ == '__main__':
    unittest.main()
