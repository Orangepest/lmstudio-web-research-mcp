from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from web_research.citations import validate_citations
from web_research.report import (
    assess_answer_readiness,
    assess_research_quality,
    build_source_policy_audit,
    build_reports,
    build_research_report,
    normalize_report_format,
    recommended_next_searches,
)


class ReportTests(unittest.TestCase):
    def test_build_research_report_includes_claims_sources_and_citations(self) -> None:
        report = build_research_report(
            {
                'question': 'LM Studio research',
                'message': 'Research completed with sources',
                'claims': [
                    {
                        'claim': 'LM Studio can use local MCP research tools.',
                        'confidence': 'medium',
                        'supporting_sources': [1],
                    }
                ],
                'sources': [
                    {
                        'source_id': 1,
                        'title': 'Docs',
                        'final_url': 'https://example.com/docs',
                        'reliability': {
                            'source_type': 'documentation',
                            'reliability_weight': 'strong',
                            'credibility': {'label': 'high', 'score': 88},
                        },
                    }
                ],
                'uncertainties': ['Some claims are supported by only one source.'],
                'recent_changes': [{'quote': 'Latest release changed MCP behavior.', 'citation': 'source:1[0:40]'}],
                'citations': ['source:1[0:40]'],
                'evidence_index': {
                    'ok': True,
                    'chunk_count': 1,
                    'coverage': {'top_chunk_source_count': 1, 'top_chunk_sources_without_extracted_evidence': []},
                    'top_chunks': [
                        {
                            'source_id': 1,
                            'score': 2.5,
                            'text': 'LM Studio can use local MCP research tools with source-grounded evidence.',
                            'matched_terms': ['studio', 'research'],
                        }
                    ],
                },
                'citation_audit': {'ok': True, 'uncited_claim_ids': [], 'unknown_report_source_ids': []},
                'source_freshness': {'current_sensitive': True, 'content_freshness_evidence': True, 'newest_mentioned_year': 2026, 'recent_change_count': 1},
                'source_selection_telemetry': {
                    'planned_read_count': 3,
                    'attempted_read_count': 2,
                    'selected_source_count': 1,
                    'planned_authority_source_count': 2,
                    'selected_authority_source_count': 1,
                    'planned_low_value_source_count': 1,
                    'planned_policy_skip_count': 0,
                    'trace_policy_skip_count': 0,
                    'duplicate_skip_count': 0,
                    'read_failure_count': 1,
                    'cache_hit_source_count': 0,
                    'read_selection_reason_counts': {'strong_source_candidate': 2},
                    'top_source_score_reasons': [{'reason': 'market_authority_domain', 'count': 1}],
                },
            }
        )

        self.assertIn('# LM Studio research', report)
        self.assertIn('LM Studio can use local MCP research tools.', report)
        self.assertIn('source:1 Docs https://example.com/docs', report)
        self.assertIn('## Source Reliability', report)
        self.assertIn('## Best Evidence', report)
        self.assertIn('## Claim Support Table', report)
        self.assertIn('## Source-Claim Contradiction Table', report)
        self.assertIn('No source-claim contradictions were identified.', report)
        self.assertIn('score 2.5', report)
        self.assertIn('## Citation Audit', report)
        self.assertIn('## Freshness Audit', report)
        self.assertIn('## Source Policy Audit', report)
        self.assertIn('## Source Selection Telemetry', report)
        self.assertIn('Authority sources: 1 selected / 2 planned.', report)
        self.assertIn('## Evidence Remediation Plan', report)
        self.assertIn('Weight: strong', report)
        self.assertIn('Credibility: high (88/100)', report)
        self.assertIn('source:1[0:40]', report)

    def test_build_reports_returns_all_report_formats(self) -> None:
        reports = build_reports(
            {
                'question': 'Compare local research tools',
                'message': 'Research completed.',
                'claims': [{'claim': 'Tool A supports search.', 'confidence': 'low', 'supporting_sources': [1]}],
                'sources': [{'source_id': 1, 'title': 'A', 'url': 'https://example.com/a'}],
                'citations': ['source:1[0:20]'],
            }
        )

        self.assertEqual(
            set(reports),
            {'quick_answer', 'source_table', 'executive_brief', 'long_report', 'comparison_matrix'},
        )
        self.assertIn('| ID | Title | URL |', reports['source_table'])
        self.assertIn('| Claim | Confidence | Supporting Sources | Indexed Evidence | Conflicting Sources |', reports['comparison_matrix'])
        self.assertIn('source:1', reports['executive_brief'])
        self.assertIn('| Tool A supports search. | low | source:1 |  |  |', reports['comparison_matrix'])

    def test_build_research_report_selects_requested_format_and_falls_back(self) -> None:
        payload = {
            'question': 'Local research',
            'message': 'Research completed.',
            'claims': [{'claim': 'Local research tools can produce several report formats.', 'confidence': 'medium'}],
        }

        self.assertTrue(build_research_report(payload, report_format='executive_brief').startswith('# Executive Brief: Local research'))
        self.assertEqual(normalize_report_format('bad_format'), 'long_report')
        self.assertIn('## Answer Snapshot', build_research_report(payload, report_format='bad_format'))

    def test_assess_research_quality_labels_strengths_and_gaps(self) -> None:
        payload = {
            'sources': [
                {'source_id': 1, 'final_url': 'https://a.example/report'},
                {'source_id': 2, 'final_url': 'https://b.example/report'},
                {'source_id': 3, 'final_url': 'https://c.example/report'},
                {'source_id': 4, 'final_url': 'https://d.example/report'},
                {'source_id': 5, 'final_url': 'https://e.example/report'},
            ],
            'source_quality': {'unique_domain_count': 5},
            'citations': ['source:1[0:10]', 'source:2[0:10]', 'source:3[0:10]', 'source:4[0:10]', 'source:5[0:10]'],
            'claims': [
                {
                    'claim': 'Local research quality can be assessed.',
                    'supporting_sources': [1, 2],
                    'conflicting_sources': [],
                }
            ],
            'failures': [],
            'blocked_sources': [],
        }

        quality = assess_research_quality(payload)

        self.assertEqual(quality['label'], 'strong')
        self.assertGreaterEqual(quality['score'], 75)
        self.assertIn('Citations validate against collected sources.', quality['strengths'])

    def test_assess_research_quality_rewards_primary_sources(self) -> None:
        quality = assess_research_quality(
            {
                'sources': [
                    {'source_id': 1, 'final_url': 'https://docs.example.com/a'},
                    {'source_id': 2, 'final_url': 'https://agency.gov/b'},
                ],
                'source_quality': {'unique_domain_count': 2, 'primary_source_count': 2},
                'citations': ['source:1[0:10]', 'source:2[0:10]'],
                'claims': [{'claim': 'Primary sources improve research confidence.', 'supporting_sources': [1, 2]}],
                'failures': [],
                'blocked_sources': [],
            }
        )

        self.assertEqual(quality['primary_source_count'], 2)
        self.assertIn('Multiple strong primary sources were included.', quality['strengths'])

    def test_assess_research_quality_rewards_indexed_evidence(self) -> None:
        quality = assess_research_quality(
            {
                'sources': [
                    {'source_id': 1, 'final_url': 'https://docs.example.com/a'},
                    {'source_id': 2, 'final_url': 'https://agency.gov/b'},
                ],
                'source_quality': {'unique_domain_count': 2, 'primary_source_count': 1},
                'citations': ['source:1[0:10]', 'source:2[0:10]'],
                'claims': [{'claim': 'Indexed evidence improves grounded reports.', 'supporting_sources': [1, 2]}],
                'failures': [],
                'blocked_sources': [],
                'evidence_index': {
                    'ok': True,
                    'chunk_count': 4,
                    'coverage': {'top_chunk_source_count': 2, 'top_chunk_sources_without_extracted_evidence': []},
                },
                'claim_support': {
                    'ok': True,
                    'supported_claim_count': 1,
                    'unsupported_claim_count': 0,
                    'multi_source_supported_claim_count': 1,
                    'claims': [],
                },
            }
        )

        self.assertEqual(quality['indexed_chunk_count'], 4)
        self.assertEqual(quality['top_chunk_source_count'], 2)
        self.assertEqual(quality['indexed_supported_claim_count'], 1)
        self.assertEqual(quality['indexed_multi_source_claim_count'], 1)
        self.assertIn('Evidence index found relevant chunks across multiple sources.', quality['strengths'])
        self.assertIn('1 claim(s) have indexed support from multiple sources.', quality['strengths'])

    def test_assess_research_quality_computes_domains_without_source_quality(self) -> None:
        quality = assess_research_quality(
            {
                'sources': [
                    {'source_id': 1, 'final_url': 'https://a.example/report'},
                    {'source_id': 2, 'final_url': 'https://b.example/report'},
                ],
                'citations': ['source:1[0:10]', 'source:2[0:10]'],
                'claims': [],
                'failures': [],
                'blocked_sources': [],
            }
        )

        self.assertEqual(quality['unique_domain_count'], 2)

    def test_source_policy_audit_counts_policy_and_recovery_skips(self) -> None:
        payload = {
            'failures': [
                {
                    'url': 'https://www.researchgate.net/publication/123',
                    'message': 'skipped by source policy: hostile_or_low_value_research_domain',
                    'skipped': True,
                    'skip_reason': 'hostile_or_low_value_research_domain',
                },
                {
                    'url': 'https://example.com/paywalled',
                    'blocked': True,
                    'recovery_skipped': True,
                    'recovery_skip_reason': 'hard_block_or_no_recovery_domain',
                },
            ],
            'selection_trace': [
                {
                    'url': 'https://www.researchgate.net/publication/123',
                    'decision': 'skipped_source_policy',
                    'skip_reason': 'hostile_or_low_value_research_domain',
                }
            ],
        }

        audit = build_source_policy_audit(payload)
        report = build_research_report({'query': 'Policy audit', **payload, 'source_policy_audit': audit})

        self.assertFalse(audit['ok'])
        self.assertEqual(audit['skipped_source_count'], 1)
        self.assertEqual(audit['trace_skipped_source_count'], 1)
        self.assertEqual(audit['hard_block_recovery_skip_count'], 1)
        self.assertEqual(audit['skip_reason_counts'], {'hostile_or_low_value_research_domain': 2})
        self.assertEqual(audit['skipped_domains'][0]['domain'], 'researchgate.net')
        self.assertIn('## Source Policy Audit', report)
        self.assertIn('Policy-skipped sources: 1', report)

    def test_assess_answer_readiness_blocks_weak_uncited_reports(self) -> None:
        readiness = assess_answer_readiness(
            {
                'sources': [],
                'claims': [{'claim': 'Unsupported claim.'}],
                'citations': [],
                'research_quality': {'label': 'weak', 'score': 12},
                'citation_validation': {'ok': False, 'citation_count': 0},
                'citation_audit': {'ok': False, 'uncited_claim_ids': [1], 'unsupported_report_sections': ['Answer']},
                'source_quality': {'unique_domain_count': 0, 'primary_source_count': 0},
                'final_answer_review': {'ok': False, 'critical_count': 1, 'high_count': 0, 'medium_count': 0},
            },
            report='Tiny answer.',
        )

        self.assertFalse(readiness['ok'])
        self.assertIn(readiness['label'], {'blocked', 'not_ready'})
        self.assertIn('Research quality is weak.', readiness['blockers'])
        self.assertIn('Citation validation failed.', readiness['blockers'])
        self.assertGreater(readiness['checks']['final_review_high_or_critical_count'], 0)

    def test_assess_answer_readiness_marks_grounded_diverse_answer_ready(self) -> None:
        readiness = assess_answer_readiness(
            {
                'sources': [
                    {'source_id': 1, 'final_url': 'https://docs.example.com/a'},
                    {'source_id': 2, 'final_url': 'https://agency.gov/b'},
                    {'source_id': 3, 'final_url': 'https://news.example.org/c'},
                ],
                'claims': [{'claim': 'Grounded claim.', 'supporting_sources': [1, 2]}],
                'citations': ['source:1[0:10]', 'source:2[0:10]'],
                'research_quality': {'label': 'strong', 'score': 88, 'conflicted_claim_count': 0},
                'citation_validation': {'ok': True, 'citation_count': 2},
                'citation_audit': {'ok': True, 'uncited_claim_ids': [], 'unsupported_report_sections': []},
                'source_quality': {'unique_domain_count': 3, 'primary_source_count': 2},
                'research_coverage': {'planned_intent_count': 2, 'satisfied_intent_count': 2},
                'source_freshness': {'current_sensitive': True, 'content_freshness_evidence': True, 'gaps': []},
                'claim_support': {'unsupported_claim_count': 0},
                'contradiction_table': {'conflicted_claim_count': 0},
                'final_answer_review': {'ok': True, 'critical_count': 0, 'high_count': 0, 'medium_count': 0},
            },
            report='This answer is grounded in multiple sources and includes enough detail to present confidently. ' * 4,
        )

        self.assertTrue(readiness['ok'])
        self.assertEqual(readiness['label'], 'ready')
        self.assertGreaterEqual(readiness['score'], 80)
        self.assertIn('Research quality is strong.', readiness['strengths'])

    def test_build_reports_includes_research_quality_in_reports(self) -> None:
        reports = build_reports(
            {
                'question': 'Thin evidence',
                'message': 'Research completed.',
                'research_quality': {
                    'label': 'weak',
                    'score': 12,
                    'strengths': [],
                    'gaps': ['No citations were produced.'],
                },
            }
        )

        self.assertIn('Research quality: weak (12/100)', reports['executive_brief'])
        self.assertIn('## Research Quality', reports['long_report'])
        self.assertIn('- Label: weak', reports['long_report'])
        self.assertIn('- Score: 12/100', reports['long_report'])
        self.assertIn('- No citations were produced.', reports['long_report'])

    def test_build_reports_includes_answer_readiness(self) -> None:
        reports = build_reports(
            {
                'question': 'Readiness',
                'message': 'Research completed.',
                'answer_readiness': {
                    'ok': False,
                    'label': 'needs_review',
                    'score': 68,
                    'blockers': [],
                    'warnings': ['Coverage is incomplete.'],
                    'strengths': ['Citations validate.'],
                },
            }
        )

        self.assertIn('## Answer Readiness', reports['executive_brief'])
        self.assertIn('Status: needs_review (68/100)', reports['long_report'])
        self.assertIn('Coverage is incomplete.', reports['executive_brief'])

    def test_validate_citations_reports_missing_sources(self) -> None:
        result = validate_citations({'sources': [{'source_id': 1}], 'citations': ['source:1[0:5]', 'source:9[0:5]']})

        self.assertFalse(result['ok'])
        self.assertEqual(result['invalid_citations'], ['source:9[0:5]'])

    def test_recommended_next_searches_uses_question_and_uncertainties(self) -> None:
        searches = recommended_next_searches({'question': 'LM Studio MCP', 'uncertainties': ['Need more official docs.']})

        self.assertIn('LM Studio MCP official source', searches)
        self.assertTrue(any('Need more official docs.' in item for item in searches))

    def test_recommended_next_searches_targets_quality_gaps(self) -> None:
        searches = recommended_next_searches(
            {
                'question': 'LM Studio MCP',
                'source_quality': {'primary_source_count': 0, 'unique_domain_count': 1},
                'research_quality': {'blocked_source_count': 1, 'conflicted_claim_count': 1},
                'source_freshness': {'current_sensitive': True, 'content_freshness_evidence': False},
                'source_selection_telemetry': {'planned_low_value_source_count': 3, 'planned_authority_source_count': 1},
                'claims': [
                    {
                        'claim_id': 1,
                        'claim': 'LM Studio MCP tools can access authenticated browser pages.',
                        'supporting_sources': [1],
                        'conflicting_sources': [2],
                    }
                ],
            },
            limit=15,
        )

        self.assertIn('LM Studio MCP primary source documentation official', searches)
        self.assertTrue(any('official documentation primary source report' in item for item in searches))
        self.assertTrue(any('official data report filing benchmark' in item for item in searches))
        self.assertIn('LM Studio MCP independent sources', searches)
        self.assertIn('LM Studio MCP alternative accessible source', searches)
        self.assertIn('LM Studio MCP contradiction verification', searches)
        self.assertTrue(any('authenticated browser pages conflicting evidence' in item for item in searches))
        self.assertIn('LM Studio MCP changelog release notes latest', searches)

    def test_build_reports_includes_source_mix(self) -> None:
        reports = build_reports(
            {
                'question': 'Source mix',
                'message': 'Research completed.',
                'source_quality': {
                    'unique_domain_count': 2,
                    'primary_source_count': 1,
                    'source_type_counts': {'documentation': 1, 'news': 1},
                    'reliability_weight_counts': {'medium': 1, 'strong': 1},
                    'credibility_label_counts': {'high': 1, 'medium': 1},
                    'average_credibility_score': 76.5,
                    'downgrade_reasons': [
                        {
                            'reason': 'thin_domain_diversity',
                            'severity': 'medium',
                            'message': 'Three or more sources were selected, but they cover fewer than three domains.',
                        }
                    ],
                },
                'research_coverage': {
                    'planned_intent_count': 2,
                    'satisfied_intent_count': 1,
                    'average_intent_quality_score': 44.5,
                    'low_quality_intents': ['primary_source'],
                    'missing_intents': ['primary_source'],
                    'by_intent': [
                        {
                            'intent': 'documentation',
                            'status': 'satisfied',
                            'quality_label': 'strong',
                            'quality_score': 82,
                            'matched_source_count': 1,
                            'selected_source_count': 1,
                            'quality_signals': ['strong_source', 'documentation_source'],
                        },
                        {
                            'intent': 'primary_source',
                            'status': 'attempted_no_sources',
                            'quality_label': 'weak',
                            'quality_score': 7,
                            'matched_source_count': 0,
                            'selected_source_count': 0,
                            'quality_signals': ['failed_or_blocked_sources'],
                        },
                    ],
                    'gaps': ['Missing or unsatisfied plan intents: primary_source.'],
                },
                'citation_audit': {
                    'ok': False,
                    'uncited_claim_ids': [2],
                    'unknown_report_source_ids': [],
                    'unsupported_report_sections': ['Extra Analysis'],
                },
                'source_freshness': {
                    'current_sensitive': True,
                    'content_freshness_evidence': False,
                    'newest_mentioned_year': 2022,
                    'recent_change_count': 0,
                    'gaps': ['No recent-change evidence snippets were extracted.'],
                },
                'final_answer_review': {
                    'ok': False,
                    'issue_count': 1,
                    'critical_count': 0,
                    'high_count': 1,
                    'medium_count': 0,
                    'low_count': 0,
                    'issues': [
                        {
                            'severity': 'high',
                            'message': 'No strong primary sources were identified.',
                            'suggested_fix': 'Search official documentation.',
                        }
                    ],
                    'contradiction_review': {
                        'ok': False,
                        'conflicted_claim_count': 1,
                        'contested_claims': [
                            {
                                'claim_id': 7,
                                'claim': 'A contested claim.',
                                'supporting_sources': [1],
                                'conflicting_sources': [2],
                            }
                        ],
                        'follow_up_searches': ['Source mix contested claim conflicting evidence'],
                    },
                },
            }
        )

        self.assertIn('Strong primary sources: 1', reports['executive_brief'])
        self.assertIn('Source types: documentation: 1, news: 1', reports['long_report'])
        self.assertIn('Credibility labels: high: 1, medium: 1', reports['executive_brief'])
        self.assertIn('Average credibility score: 76.5/100', reports['long_report'])
        self.assertIn('Downgrade (medium): Three or more sources were selected', reports['executive_brief'])
        self.assertIn('Downgrade (medium): Three or more sources were selected', reports['long_report'])
        self.assertIn('Plan coverage: 1/2 intent(s) satisfied.', reports['executive_brief'])
        self.assertIn('Average intent source quality: 44.5/100', reports['executive_brief'])
        self.assertIn('Intent documentation: satisfied quality strong (82/100)', reports['long_report'])
        self.assertIn('Low-quality intents: primary_source.', reports['executive_brief'])
        self.assertIn('Missing intents: primary_source.', reports['long_report'])
        self.assertIn('Claims without evidence citations: 1', reports['executive_brief'])
        self.assertIn('Unsupported report sections: Extra Analysis', reports['long_report'])
        self.assertIn('Content freshness evidence: False', reports['executive_brief'])
        self.assertIn('## Final Answer Review', reports['executive_brief'])
        self.assertIn('Contradicted claims: 1', reports['executive_brief'])
        self.assertIn('## Source-Claim Contradictions', reports['executive_brief'])
        self.assertIn('Source mix contested claim conflicting evidence', reports['long_report'])
        self.assertIn('No strong primary sources were identified', reports['long_report'])

    def test_build_reports_includes_source_claim_contradiction_table(self) -> None:
        reports = build_reports(
            {
                'question': 'Contradiction table',
                'message': 'Research completed.',
                'sources': [
                    {'source_id': 1, 'title': 'Official Docs', 'final_url': 'https://docs.example.com/a'},
                    {'source_id': 2, 'title': 'Issue Thread', 'final_url': 'https://github.com/example/issues/1'},
                ],
                'claims': [
                    {
                        'claim_id': 3,
                        'claim': 'The tool supports browser automation by default.',
                        'supporting_sources': [1],
                        'conflicting_sources': [2],
                        'confidence': 'low',
                        'conflict_reviews': [
                            {
                                'method': 'local_llm',
                                'against_claim_id': 4,
                                'verdict': 'contradiction',
                                'reason': 'One source says default support exists and another says setup is required.',
                            }
                        ],
                    }
                ],
                'final_answer_review': {
                    'ok': False,
                    'issue_count': 1,
                    'critical_count': 0,
                    'high_count': 1,
                    'medium_count': 0,
                    'low_count': 0,
                    'issues': [],
                    'contradiction_review': {
                        'ok': False,
                        'conflicted_claim_count': 1,
                        'contested_claims': [
                            {
                                'claim_id': 3,
                                'claim': 'The tool supports browser automation by default.',
                                'supporting_sources': [1],
                                'conflicting_sources': [2],
                                'confidence': 'low',
                                'conflict_reviews': [
                                    {
                                        'method': 'local_llm',
                                        'verdict': 'contradiction',
                                        'reason': 'One source says default support exists and another says setup is required.',
                                    }
                                ],
                            }
                        ],
                        'retrieval_plan': [
                            {
                                'claim_id': 3,
                                'query': 'browser automation default support official clarification',
                                'intent': 'contradiction_resolution',
                            }
                        ],
                        'follow_up_searches': ['browser automation default support conflicting evidence'],
                    },
                },
            }
        )

        self.assertIn('## Source-Claim Contradiction Table', reports['long_report'])
        self.assertIn('| Claim | Supporting Sources | Conflicting Sources | Status | Follow-Up |', reports['long_report'])
        self.assertIn('source:1 Official Docs', reports['long_report'])
        self.assertIn('source:2 Issue Thread', reports['long_report'])
        self.assertIn('needs_resolution', reports['executive_brief'])
        self.assertIn('browser automation default support official clarification', reports['long_report'])

    def test_finalize_report_payload_records_structured_contradiction_table(self) -> None:
        from web_research.report import finalize_report_payload

        payload = {
            'question': 'Contradiction table payload',
            'message': 'Research completed.',
            'sources': [
                {'source_id': 1, 'title': 'Official Docs', 'final_url': 'https://docs.example.com/a'},
                {'source_id': 2, 'title': 'Issue Thread', 'final_url': 'https://github.com/example/issues/1'},
            ],
            'claims': [
                {
                    'claim_id': 3,
                    'claim': 'The tool supports browser automation by default.',
                    'supporting_sources': [1],
                    'conflicting_sources': [2],
                    'confidence': 'low',
                }
            ],
            'citations': ['source:1[0:10]', 'source:2[0:10]'],
            'citation_validation': {'ok': True, 'citation_count': 2},
            'research_quality': {'label': 'moderate', 'score': 60, 'strengths': [], 'gaps': []},
        }

        asyncio.run(finalize_report_payload(payload, report_format='long_report'))

        table = payload['contradiction_table']
        self.assertEqual(table['conflicted_claim_count'], 1)
        self.assertEqual(table['rows'][0]['claim_id'], 3)
        self.assertEqual(table['rows'][0]['supporting_sources'], [1])
        self.assertEqual(table['rows'][0]['conflicting_sources'], [2])
        self.assertEqual(table['rows'][0]['resolution_status'], 'needs_resolution')
        self.assertIn('answer_readiness', payload)
        self.assertFalse(payload['answer_readiness']['ok'])
        self.assertIn('contradicted claim', ' '.join(payload['answer_readiness']['blockers']).lower())
        self.assertIn('## Source-Claim Contradiction Table', payload['final_report'])
        self.assertIn('## Answer Readiness', payload['final_report'])

    def test_finalize_report_payload_falls_back_when_synthesis_rejected(self) -> None:
        from web_research.report import finalize_report_payload

        payload = {
            'question': 'topic',
            'message': 'Research completed.',
            'sources': [{'source_id': 1, 'title': 'Source', 'url': 'https://example.com'}],
            'claims': [{'claim': 'Claim one.', 'supporting_sources': [1]}],
            'citations': ['source:1[0:10]'],
            'citation_validation': {'ok': True, 'citation_count': 1},
            'research_quality': {'label': 'moderate', 'score': 60, 'strengths': [], 'gaps': []},
        }

        with patch(
            'web_research.local_llm.synthesize_research_report',
            return_value={
                'enabled': True,
                'used': False,
                'message': 'Local LLM report synthesis rejected by validation.',
                'validation': {'ok': False, 'issues': ['bad citation']},
            },
        ):
            asyncio.run(finalize_report_payload(payload, report_format='executive_brief'))

        self.assertFalse(payload['report_synthesis']['used'])
        self.assertIn('rejected', payload['report_synthesis']['message'])
        self.assertEqual(payload['final_report'], payload['reports']['executive_brief'])
        self.assertIn('final_answer_review', payload)
        self.assertIn('## Final Answer Review', payload['final_report'])

    def test_finalize_report_payload_refreshes_reports_after_synthesis_review(self) -> None:
        from web_research.report import finalize_report_payload

        payload = {
            'question': 'Synth contradiction',
            'message': 'Research completed.',
            'sources': [
                {'source_id': 1, 'title': 'Capability Docs', 'url': 'https://docs.example.com/a'},
                {'source_id': 2, 'title': 'Security Policy', 'url': 'https://security.example.org/b'},
            ],
            'claims': [
                {
                    'claim_id': 1,
                    'claim': 'Browser automation supports silent cross-site actions.',
                    'supporting_sources': [1],
                    'conflicting_sources': [2],
                }
            ],
            'citations': ['source:1[0:10]', 'source:2[0:10]'],
            'citation_validation': {'ok': True, 'citation_count': 2},
            'citation_audit': {'ok': True, 'issues': []},
            'research_quality': {'label': 'moderate', 'score': 60, 'strengths': [], 'gaps': []},
            'recommended_next_searches': ['browser automation silent cross-site actions official clarification'],
        }

        with patch(
            'web_research.local_llm.synthesize_research_report',
            return_value={
                'enabled': True,
                'used': True,
                'message': 'Local synthesis used.',
                'validation': {'ok': True, 'issues': []},
                'report': 'Synthesized answer cites source:1 and source:2.',
            },
        ):
            asyncio.run(finalize_report_payload(payload, report_format='long_report'))

        self.assertEqual(payload['final_report'], 'Synthesized answer cites source:1 and source:2.')
        self.assertEqual(payload['contradiction_table']['conflicted_claim_count'], 1)
        self.assertIn('| Browser automation supports silent cross-site actions.', payload['reports']['long_report'])
        self.assertIn('Contradicted claims: 1', payload['reports']['long_report'])


if __name__ == '__main__':
    unittest.main()
