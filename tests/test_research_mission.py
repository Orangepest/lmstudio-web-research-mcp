from __future__ import annotations

import unittest
from unittest.mock import patch

from mcp_server.server import _parse_safe_mission_request, safe_research_mission


class ResearchMissionTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_safe_mission_request_extracts_options_and_question(self) -> None:
        parsed = _parse_safe_mission_request(
            """
            Compare local AI research tools.
            profile: exhaustive
            export=true
            source_pack=true
            """
        )

        self.assertEqual(parsed['question'], 'Compare local AI research tools.')
        self.assertEqual(parsed['options']['profile'], 'exhaustive')
        self.assertEqual(parsed['options']['export'], 'true')
        self.assertEqual(parsed['options']['source_pack'], 'true')

    def test_parse_safe_mission_request_accepts_query_as_question(self) -> None:
        parsed = _parse_safe_mission_request(
            """
            query: Compare local AI research tools.
            profile: careful
            """
        )

        self.assertEqual(parsed['question'], 'Compare local AI research tools.')
        self.assertEqual(parsed['options']['profile'], 'careful')

    async def test_safe_research_mission_dry_run_returns_plan_without_research(self) -> None:
        with patch('mcp_server.server._run_deep_research') as deep_research:
            result = await safe_research_mission('question: Compare local tools\nprofile=fast\ndry_run=true\nexport=true')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['profile']['name'], 'fast')
        self.assertEqual(result['planned_research']['breadth'], 2)
        self.assertTrue(result['planned_packaging']['export'])
        deep_research.assert_not_called()

    async def test_safe_research_mission_runs_profile_deep_research_and_quality_gate(self) -> None:
        payload = {
            'ok': True,
            'run_id': 'run-1',
            'run_path': '/tmp/run-1/run.json',
            'final_report_path': '/tmp/run-1/report.md',
            'report_format': 'executive_brief',
            'sources': [{'source_id': 1}],
            'evidence': [{'citation': 'source:1[0:10]'}],
            'claims': [{'claim_id': 1}],
            'failures': [],
            'blocked_sources': [],
            'research_quality': {'label': 'moderate', 'score': 65},
            'source_quality': {'selected_source_count': 1},
            'recommended_next_searches': ['next'],
        }
        with patch('mcp_server.server._run_deep_research', return_value=payload) as deep_research:
            result = await safe_research_mission('Compare local tools\nprofile=careful')

        self.assertTrue(result['ok'])
        self.assertEqual(result['profile'], 'careful')
        self.assertEqual(result['run_id'], 'run-1')
        self.assertEqual(result['quality_gate']['min_score'], 60)
        self.assertTrue(result['quality_gate']['ok'])
        self.assertEqual(result['counts']['sources'], 1)
        self.assertIn('payload', result)
        deep_research.assert_awaited_once()
        kwargs = deep_research.call_args.kwargs
        self.assertEqual(kwargs['breadth'], 4)
        self.assertEqual(kwargs['read_top_per_query'], 1)
        self.assertEqual(kwargs['follow_up_rounds'], 1)

    async def test_safe_research_mission_can_fail_quality_gate(self) -> None:
        payload = {
            'ok': True,
            'run_id': 'run-1',
            'sources': [],
            'evidence': [],
            'claims': [],
            'failures': [],
            'blocked_sources': [],
            'research_quality': {'label': 'thin', 'score': 40},
        }
        with patch('mcp_server.server._run_deep_research', return_value=payload):
            result = await safe_research_mission('Compare local tools\nprofile=careful')

        self.assertFalse(result['ok'])
        self.assertFalse(result['quality_gate']['ok'])
        self.assertEqual(result['quality_gate']['score'], 40)

    async def test_safe_research_mission_fails_when_answer_not_ready(self) -> None:
        payload = {
            'ok': True,
            'run_id': 'run-1',
            'sources': [{'source_id': 1}],
            'evidence': [{'citation': 'source:1[0:10]'}],
            'claims': [{'claim_id': 1}],
            'failures': [],
            'blocked_sources': [],
            'research_quality': {'label': 'strong', 'score': 86},
            'answer_readiness': {
                'ok': False,
                'label': 'not_ready',
                'score': 48,
                'blockers': ['Citation validation failed.'],
                'warnings': ['Needs more primary evidence.'],
            },
        }
        with patch('mcp_server.server._run_deep_research', return_value=payload):
            result = await safe_research_mission('Compare local tools\nprofile=careful')

        self.assertFalse(result['ok'])
        self.assertFalse(result['quality_gate']['ok'])
        self.assertTrue(result['quality_gate']['score_gate_ok'])
        self.assertFalse(result['quality_gate']['answer_gate_ok'])
        self.assertEqual(result['quality_gate']['answer_readiness_label'], 'not_ready')

    async def test_safe_research_mission_skips_packaging_when_quality_gate_fails(self) -> None:
        payload = {
            'ok': True,
            'run_id': 'run-1',
            'sources': [],
            'evidence': [],
            'claims': [],
            'failures': [],
            'blocked_sources': [],
            'research_quality': {'label': 'thin', 'score': 40},
        }
        with patch('mcp_server.server._run_deep_research', return_value=payload), patch(
            'mcp_server.server.export_research_run'
        ) as export_run, patch('mcp_server.server.collect_source_pack') as collect_pack:
            result = await safe_research_mission('Compare local tools\nprofile=careful\nexport=true\nsource_pack=true')

        self.assertFalse(result['ok'])
        self.assertTrue(result['packaging']['skipped'])
        self.assertEqual(result['packaging']['reason'], 'quality_gate_failed')
        export_run.assert_not_called()
        collect_pack.assert_not_called()

    async def test_safe_research_mission_can_package_failed_gate_when_requested(self) -> None:
        payload = {
            'ok': True,
            'run_id': 'run-1',
            'sources': [],
            'evidence': [],
            'claims': [],
            'failures': [],
            'blocked_sources': [],
            'research_quality': {'label': 'thin', 'score': 40},
        }
        with patch('mcp_server.server._run_deep_research', return_value=payload), patch(
            'mcp_server.server.export_research_run',
            return_value={'ok': True, 'run_id': 'run-1', 'bundle_dir': '/tmp/export'},
        ) as export_run:
            result = await safe_research_mission('Compare local tools\nprofile=careful\nexport=true\npackage_on_fail=true')

        self.assertFalse(result['ok'])
        self.assertTrue(result['packaging']['export']['ok'])
        self.assertEqual(result['packaging']['export']['run_ids'], ['run-1'])
        self.assertEqual(result['packaging']['export']['run_count'], 1)
        export_run.assert_called_once()

    async def test_safe_research_mission_can_package_persisted_run(self) -> None:
        payload = {
            'ok': True,
            'run_id': 'run-1',
            'sources': [{'source_id': 1}],
            'evidence': [],
            'claims': [],
            'failures': [],
            'blocked_sources': [],
            'research_quality': {'label': 'moderate', 'score': 65},
        }
        with patch('mcp_server.server._run_deep_research', return_value=payload), patch(
            'mcp_server.server.export_research_run',
            return_value={'ok': True, 'run_id': 'run-1', 'bundle_dir': '/tmp/export'},
        ) as export_run, patch(
            'mcp_server.server.collect_source_pack',
            return_value={'ok': True, 'redacted': True, 'counts': {'runs': 1}},
        ) as collect_pack, patch(
            'mcp_server.server.write_source_pack',
            return_value={'ok': True, 'output_dir': '/tmp/pack'},
        ) as write_pack:
            result = await safe_research_mission('Compare local tools\nprofile=private-share\nexport=true\nsource_pack=true')

        self.assertTrue(result['ok'])
        self.assertTrue(result['packaging']['export']['ok'])
        self.assertTrue(result['packaging']['source_pack']['ok'])
        export_run.assert_called_once()
        self.assertTrue(export_run.call_args.kwargs['redact'])
        collect_pack.assert_called_once_with(['run-1'], redact=True)
        write_pack.assert_called_once()


if __name__ == '__main__':
    unittest.main()
