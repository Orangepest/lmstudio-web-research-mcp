from __future__ import annotations

import io
import unittest

from pypdf import PdfWriter

from web_research.extract import classify_block_type, detect_blocked_page, extract_html, extract_pdf
from web_research.rank import extract_evidence


class ExtractRankTests(unittest.TestCase):
    def test_extract_html_prefers_article_text(self) -> None:
        html = '''
        <html><head><title>Sample</title></head>
        <body><nav>ignore me</nav><article><h1>Heading</h1><p>Useful research text appears here.</p></article></body></html>
        '''

        extracted = extract_html(html)

        self.assertEqual(extracted.title, 'Sample')
        self.assertIn('Heading: Useful research text appears here.', extracted.text)

    def test_detect_blocked_page(self) -> None:
        marker = detect_blocked_page('<html>Please verify you are human</html>', 'Just a moment')

        self.assertEqual(marker, 'verify you are human')

    def test_classify_block_type(self) -> None:
        self.assertEqual(classify_block_type('captcha'), 'captcha')
        self.assertEqual(classify_block_type('access denied'), 'blocked')

    def test_extract_pdf_handles_empty_pdf(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        buffer = io.BytesIO()
        writer.write(buffer)

        extracted = extract_pdf(buffer.getvalue())

        self.assertEqual(extracted.text, '')

    def test_extract_evidence_scores_query_blocks(self) -> None:
        text = 'Intro text.\n\nPricing includes online retrieval and citations.\n\nContact us.'

        evidence = extract_evidence(text, 'retrieval citations', source_id=2, url='https://example.com', title='Example')

        self.assertEqual(evidence[0]['source_id'], 2)
        self.assertIn('retrieval and citations', evidence[0]['quote'])
        self.assertEqual(evidence[0]['citation'].split('[')[0], 'source:2')


if __name__ == '__main__':
    unittest.main()
