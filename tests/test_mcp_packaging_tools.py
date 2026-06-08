from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_server.server import (
    _parse_safe_packaging_request,
    safe_build_source_pack,
    safe_export_research_run,
)


class MCPPackagingToolsTests(unittest.TestCase):
    def test_parse_safe_packaging_request_accepts_values_options_and_flags(self) -> None:
        parsed = _parse_safe_packaging_request(
            """
            run-123
            profile=private-share
            zip=true
            redact
            """
        )

        self.assertEqual(parsed['values'], ['run-123'])
        self.assertEqual(parsed['options']['profile'], 'private-share')
        self.assertEqual(parsed['options']['zip'], 'true')
        self.assertTrue(parsed['options']['redact'])

    def test_parse_safe_packaging_request_accepts_bullets_commas_and_run_ids_option(self) -> None:
        parsed = _parse_safe_packaging_request(
            """
            - run-1, run-2
            * run-3
            run_ids=run-4, run-5
            run_ids run-6, run-7
            """
        )

        self.assertEqual(parsed['values'], ['run-4', 'run-5', 'run-1', 'run-2', 'run-3', 'run-6', 'run-7'])

    def test_parse_safe_packaging_request_accepts_known_colon_options(self) -> None:
        parsed = _parse_safe_packaging_request(
            """
            run_id: run-1
            run_ids: run-2, run-3
            profile: private-share
            dry-run: true
            find: current topic
            """
        )

        self.assertEqual(parsed['values'], ['run-2', 'run-3', 'run-1'])
        self.assertEqual(parsed['options']['profile'], 'private-share')
        self.assertEqual(parsed['options']['dry_run'], 'true')
        self.assertEqual(parsed['options']['find'], 'current topic')

    def test_parse_safe_packaging_request_leaves_unknown_colon_text_as_value(self) -> None:
        parsed = _parse_safe_packaging_request('note: run-1')

        self.assertEqual(parsed['values'], ['note: run-1'])
        self.assertEqual(parsed['options'], {})

    def test_safe_export_research_run_accepts_latest_colon_selector(self) -> None:
        with patch(
            'mcp_server.server.select_research_run_ids',
            return_value={'ok': True, 'selector': 'latest:2', 'run_ids': ['run-1', 'run-2']},
        ) as select_runs, patch(
            'mcp_server.server.export_research_runs',
            return_value={'ok': True, 'exported_count': 2},
        ):
            result = safe_export_research_run('latest: 2')

        self.assertTrue(result['ok'])
        self.assertEqual(result['run_ids'], ['run-1', 'run-2'])
        select_runs.assert_called_once_with(latest=2)

    def test_safe_export_research_run_dedupes_repeated_explicit_ids(self) -> None:
        with patch(
            'mcp_server.server.export_research_runs',
            return_value={'ok': True, 'exported_count': 2},
        ) as export_many:
            result = safe_export_research_run('run-1, run-2\n- run-1')

        self.assertTrue(result['ok'])
        self.assertEqual(result['selector'], 'explicit')
        self.assertEqual(result['run_ids'], ['run-1', 'run-2'])
        self.assertEqual(result['run_count'], 2)
        export_many.assert_called_once()
        self.assertEqual(export_many.call_args.args[0], ['run-1', 'run-2'])

    def test_safe_export_research_run_exports_single_run_with_profile_redaction(self) -> None:
        with patch(
            'mcp_server.server.export_research_run',
            return_value={'ok': True, 'bundle_dir': '/tmp/export/run-1', 'redacted': True},
        ) as export_one:
            result = safe_export_research_run('run-1\nprofile=private-share\nzip=true')

        self.assertTrue(result['ok'])
        self.assertEqual(result['tool'], 'safe_export_research_run')
        export_one.assert_called_once()
        kwargs = export_one.call_args.kwargs
        self.assertEqual(export_one.call_args.args[0], 'run-1')
        self.assertIsInstance(kwargs['output_dir'], Path)
        self.assertTrue(kwargs['redact'])
        self.assertTrue(kwargs['zip_bundle'])

    def test_safe_export_research_run_can_batch_latest_runs(self) -> None:
        with patch(
            'mcp_server.server.select_research_run_ids',
            return_value={'ok': True, 'selector': 'latest:2', 'run_ids': ['run-1', 'run-2']},
        ) as select_runs, patch(
            'mcp_server.server.export_research_runs',
            return_value={'ok': True, 'exported_count': 2},
        ) as export_many:
            result = safe_export_research_run('latest=2\nredact=true')

        self.assertTrue(result['ok'])
        self.assertEqual(result['selector'], 'latest:2')
        self.assertEqual(result['run_ids'], ['run-1', 'run-2'])
        self.assertEqual(result['run_count'], 2)
        select_runs.assert_called_once_with(latest=2)
        export_many.assert_called_once()
        self.assertEqual(export_many.call_args.args[0], ['run-1', 'run-2'])
        self.assertTrue(export_many.call_args.kwargs['redact'])

    def test_safe_export_research_run_dry_run_previews_without_writing_files(self) -> None:
        with patch(
            'mcp_server.server.export_research_run',
        ) as export_one:
            result = safe_export_research_run('run-1\npreview=true\nprofile=private-share')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['tool'], 'safe_export_research_run')
        self.assertEqual(result['run_ids'], ['run-1'])
        self.assertEqual(result['run_count'], 1)
        self.assertTrue(result['redacted'])
        self.assertIn('planned_output_root', result)
        export_one.assert_not_called()

    def test_safe_export_research_run_empty_dry_run_is_not_successful(self) -> None:
        with patch(
            'mcp_server.server.select_research_run_ids',
            return_value={'ok': True, 'selector': 'latest:1', 'run_ids': []},
        ):
            result = safe_export_research_run('latest=1\ndry_run=true')

        self.assertFalse(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['run_ids'], [])
        self.assertEqual(result['run_count'], 0)
        self.assertIn('selected no research runs', result['message'])

    def test_safe_build_source_pack_defaults_to_redacted_latest_pack(self) -> None:
        with patch(
            'mcp_server.server.select_research_run_ids',
            return_value={'ok': True, 'selector': 'latest:1', 'run_ids': ['run-1']},
        ) as select_runs, patch(
            'mcp_server.server.collect_source_pack',
            return_value={'ok': True, 'redacted': True, 'counts': {'runs': 1}},
        ) as collect_pack, patch(
            'mcp_server.server.write_source_pack',
            return_value={'ok': True, 'output_dir': '/tmp/pack', 'redacted': True},
        ) as write_pack:
            result = safe_build_source_pack('')

        self.assertTrue(result['ok'])
        self.assertEqual(result['tool'], 'safe_build_source_pack')
        self.assertEqual(result['run_ids'], ['run-1'])
        self.assertEqual(result['run_count'], 1)
        select_runs.assert_called_once_with(latest=1)
        collect_pack.assert_called_once_with(['run-1'], redact=True)
        write_pack.assert_called_once()

    def test_safe_build_source_pack_dry_run_previews_without_writing_files(self) -> None:
        with patch(
            'mcp_server.server.select_research_run_ids',
            return_value={'ok': True, 'selector': 'latest:3', 'run_ids': ['run-1', 'run-2', 'run-3']},
        ) as select_runs, patch(
            'mcp_server.server.collect_source_pack',
        ) as collect_pack, patch(
            'mcp_server.server.write_source_pack',
        ) as write_pack:
            result = safe_build_source_pack('latest=3\ndry_run=true')

        self.assertTrue(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['selector'], 'latest:3')
        self.assertEqual(result['run_ids'], ['run-1', 'run-2', 'run-3'])
        self.assertEqual(result['run_count'], 3)
        self.assertTrue(result['redacted'])
        select_runs.assert_called_once_with(latest=3)
        collect_pack.assert_not_called()
        write_pack.assert_not_called()

    def test_safe_build_source_pack_empty_dry_run_is_not_successful(self) -> None:
        with patch(
            'mcp_server.server.select_research_run_ids',
            return_value={'ok': True, 'selector': 'latest:1', 'run_ids': []},
        ):
            result = safe_build_source_pack('latest=1\ndry_run=true')

        self.assertFalse(result['ok'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['run_ids'], [])
        self.assertEqual(result['run_count'], 0)
        self.assertIn('selected no research runs', result['message'])

    def test_safe_build_source_pack_dedupes_selector_results_before_writing(self) -> None:
        with patch(
            'mcp_server.server.select_research_run_ids',
            return_value={'ok': True, 'selector': 'find:topic', 'run_ids': ['run-1', 'run-2', 'run-1']},
        ), patch(
            'mcp_server.server.collect_source_pack',
            return_value={'ok': True, 'redacted': True, 'counts': {'runs': 2}},
        ) as collect_pack, patch(
            'mcp_server.server.write_source_pack',
            return_value={'ok': True, 'output_dir': '/tmp/pack', 'redacted': True},
        ):
            result = safe_build_source_pack('find=topic')

        self.assertTrue(result['ok'])
        self.assertEqual(result['run_ids'], ['run-1', 'run-2'])
        self.assertEqual(result['run_count'], 2)
        collect_pack.assert_called_once_with(['run-1', 'run-2'], redact=True)

    def test_safe_build_source_pack_allows_explicit_unredacted_pack(self) -> None:
        with patch(
            'mcp_server.server.collect_source_pack',
            return_value={'ok': True, 'redacted': False, 'counts': {'runs': 1}},
        ) as collect_pack, patch(
            'mcp_server.server.write_source_pack',
            return_value={'ok': True, 'output_dir': '/tmp/pack', 'redacted': False},
        ):
            result = safe_build_source_pack('run-1\nunredacted')

        self.assertTrue(result['ok'])
        collect_pack.assert_called_once_with(['run-1'], redact=False)


if __name__ == '__main__':
    unittest.main()
