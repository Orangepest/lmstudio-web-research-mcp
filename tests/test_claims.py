from __future__ import annotations

import unittest

from web_research.claims import extract_claims_from_evidence, recent_change_notes, uncertainty_notes


class ClaimsTests(unittest.TestCase):
    def test_extract_claims_deduplicates_and_tracks_supporting_sources(self) -> None:
        evidence = [
            {
                'source_id': 1,
                'url': 'https://example.com/a',
                'title': 'A',
                'quote': 'LM Studio supports MCP servers through local configuration.',
                'citation': 'source:1[0:58]',
            },
            {
                'source_id': 2,
                'url': 'https://example.com/b',
                'title': 'B',
                'quote': 'LM Studio supports MCP servers through local configuration.',
                'citation': 'source:2[0:58]',
            },
        ]

        claims = extract_claims_from_evidence(evidence)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['supporting_sources'], [1, 2])
        self.assertEqual(claims[0]['confidence'], 'medium')
        self.assertEqual(len(claims[0]['supporting_evidence']), 2)

    def test_extract_claims_deduplicates_small_wording_variants(self) -> None:
        evidence = [
            {
                'source_id': 1,
                'url': 'https://example.com/a',
                'title': 'A',
                'quote': 'LM Studio supports MCP servers through local configuration.',
                'citation': 'source:1[0:58]',
            },
            {
                'source_id': 2,
                'url': 'https://example.com/b',
                'title': 'B',
                'quote': 'LM Studio supports MCP servers via local configuration.',
                'citation': 'source:2[0:54]',
            },
        ]

        claims = extract_claims_from_evidence(evidence)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['supporting_sources'], [1, 2])
        self.assertEqual(claims[0]['confidence'], 'medium')

    def test_extract_claims_marks_obvious_conflicts(self) -> None:
        evidence = [
            {
                'source_id': 1,
                'url': 'https://example.com/a',
                'title': 'A',
                'quote': 'LM Studio supports MCP servers through local configuration.',
                'citation': 'source:1[0:58]',
            },
            {
                'source_id': 2,
                'url': 'https://example.com/b',
                'title': 'B',
                'quote': 'LM Studio does not support MCP servers through local configuration.',
                'citation': 'source:2[0:67]',
            },
        ]

        claims = extract_claims_from_evidence(evidence)

        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0]['conflicting_sources'], [2])
        self.assertEqual(claims[1]['conflicting_sources'], [1])
        self.assertIn('Potential conflict found in another source.', claims[0]['source_quality_notes'])

    def test_extract_claims_marks_unsupported_as_conflict(self) -> None:
        evidence = [
            {
                'source_id': 1,
                'quote': 'LM Studio supports MCP servers through local configuration.',
                'citation': 'source:1[0:58]',
            },
            {
                'source_id': 2,
                'quote': 'LM Studio MCP servers are unsupported through local configuration.',
                'citation': 'source:2[0:64]',
            },
        ]

        claims = extract_claims_from_evidence(evidence)

        self.assertEqual(claims[0]['conflicting_sources'], [2])
        self.assertEqual(claims[1]['conflicting_sources'], [1])

    def test_uncertainty_notes_reports_single_source_claims(self) -> None:
        claims = [
            {
                'claim': 'One source claim',
                'supporting_sources': [1],
                'supporting_evidence': [],
            }
        ]

        notes = uncertainty_notes(claims=claims, failures=[], blocked_sources=[])

        self.assertIn('Some claims are supported by only one source.', notes)

    def test_recent_change_notes_finds_release_language(self) -> None:
        evidence = [
            {
                'source_id': 1,
                'citation': 'source:1[0:40]',
                'quote': 'The latest release changed MCP server behavior.',
            }
        ]

        notes = recent_change_notes(evidence)

        self.assertEqual(notes[0]['citation'], 'source:1[0:40]')

    def test_extract_claims_skips_navigation_text(self) -> None:
        evidence = [
            {
                'source_id': 1,
                'url': 'https://lmstudio.ai/docs/app/mcp',
                'title': 'Use MCP Servers | LM Studio',
                'quote': 'LM Studio Search Ctrl K App Welcome to LM Studio Docs! Offline Operation System Requirements Getting Started.',
                'citation': 'source:1[0:100]',
            },
            {
                'source_id': 1,
                'url': 'https://lmstudio.ai/docs/app/mcp',
                'title': 'Use MCP Servers | LM Studio',
                'quote': 'LM Studio can connect MCP servers to make tools available inside supported chats.',
                'citation': 'source:1[101:180]',
            },
        ]

        claims = extract_claims_from_evidence(evidence)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['claim'], 'LM Studio can connect MCP servers to make tools available inside supported chats.')

    def test_extract_claims_skips_markdown_frontmatter(self) -> None:
        evidence = [
            {
                'source_id': 1,
                'url': 'https://raw.githubusercontent.com/example/docs/main/changelog.md',
                'title': 'Changelog',
                'quote': '--- title: API Changelog description: Updates and changes to the LM Studio API.',
                'citation': 'source:1[0:80]',
            },
            {
                'source_id': 1,
                'url': 'https://raw.githubusercontent.com/example/docs/main/changelog.md',
                'title': 'Changelog',
                'quote': 'LM Studio added an Anthropic-compatible messages endpoint for local API use.',
                'citation': 'source:1[81:160]',
            },
        ]

        claims = extract_claims_from_evidence(evidence)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['claim'], 'LM Studio added an Anthropic-compatible messages endpoint for local API use.')

    def test_extract_claims_cleans_markdown_prefixes(self) -> None:
        evidence = [
            {
                'source_id': 1,
                'url': 'https://raw.githubusercontent.com/example/docs/main/changelog.md',
                'title': 'Changelog',
                'quote': 'index: 2 --- --- ###### LM Studio 0.4.1 ### Anthropic-compatible API - New endpoint is available.',
                'citation': 'source:1[0:100]',
            }
        ]

        claims = extract_claims_from_evidence(evidence)

        self.assertEqual(claims[0]['claim'], 'LM Studio 0.4.1 Anthropic-compatible API - New endpoint is available.')


if __name__ == '__main__':
    unittest.main()
