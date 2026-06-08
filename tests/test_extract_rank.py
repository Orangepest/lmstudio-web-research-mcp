from __future__ import annotations

import io
import unittest
import zipfile

from bs4 import BeautifulSoup
from pypdf import PdfWriter

from web_research.extract import (
    classify_block_type,
    clean_pdf_page_text,
    detect_blocked_page,
    extract_html,
    extract_docx,
    extract_pdf,
    extract_table_text,
)
from web_research.rank import extract_evidence


class ExtractRankTests(unittest.TestCase):
    def _docx_bytes(self) -> bytes:
        buffer = io.BytesIO()
        document_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:r><w:t>Local research agents can read Word documents.</w:t></w:r></w:p>
            <w:tbl>
              <w:tr>
                <w:tc><w:p><w:r><w:t>Plan</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>Limit</w:t></w:r></w:p></w:tc>
              </w:tr>
              <w:tr>
                <w:tc><w:p><w:r><w:t>Free</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>10 searches</w:t></w:r></w:p></w:tc>
              </w:tr>
            </w:tbl>
          </w:body>
        </w:document>'''
        core_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
          xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Research Plan</dc:title>
          <dc:creator>Research Bot</dc:creator>
        </cp:coreProperties>'''
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('word/document.xml', document_xml)
            archive.writestr('docProps/core.xml', core_xml)
        return buffer.getvalue()

    def test_extract_html_prefers_article_text(self) -> None:
        html = '''
        <html><head><title>Sample</title></head>
        <body><nav>ignore me</nav><article><h1>Heading</h1><p>Useful research text appears here.</p></article></body></html>
        '''

        extracted = extract_html(html)

        self.assertEqual(extracted.title, 'Sample')
        self.assertIn('Heading: Useful research text appears here.', extracted.text)

    def test_extract_html_preserves_table_rows(self) -> None:
        html = '''
        <html><body><main>
          <h1>Pricing</h1>
          <table>
            <tr><th>Plan</th><th>Price</th><th>Limit</th></tr>
            <tr><td>Free</td><td>$0</td><td>10 searches</td></tr>
            <tr><td>Pro</td><td>$20</td><td>Unlimited</td></tr>
          </table>
        </main></body></html>
        '''

        extracted = extract_html(html)

        self.assertIn('Table:', extracted.text)
        self.assertIn('Plan | Price | Limit', extracted.text)
        self.assertIn('Free | $0 | 10 searches', extracted.text)
        self.assertIn('Pro | $20 | Unlimited', extracted.text)
        self.assertNotIn('\nFree\n', extracted.text)

    def test_extract_table_text_returns_empty_for_empty_table(self) -> None:
        html = '<table><tr><td> </td></tr></table>'

        table = BeautifulSoup(html, 'html.parser').table

        self.assertEqual(extract_table_text(table), '')

    def test_detect_blocked_page(self) -> None:
        marker = detect_blocked_page('<html>Please verify you are human</html>', 'Just a moment')

        self.assertEqual(marker, 'verify you are human')

    def test_classify_block_type(self) -> None:
        self.assertEqual(classify_block_type('captcha'), 'captcha')
        self.assertEqual(classify_block_type('access denied'), 'blocked')

    def test_extract_pdf_handles_empty_pdf(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.add_metadata({'/Title': 'Blank Metadata PDF'})
        buffer = io.BytesIO()
        writer.write(buffer)

        extracted = extract_pdf(buffer.getvalue())

        self.assertEqual(extracted.text, '')
        self.assertEqual(extracted.title, 'Blank Metadata PDF')
        self.assertEqual(extracted.metadata['document_type'], 'pdf')
        self.assertEqual(extracted.metadata['page_count'], 1)
        self.assertEqual(extracted.metadata['extracted_page_count'], 0)

    def test_extract_docx_reads_paragraphs_tables_and_metadata(self) -> None:
        extracted = extract_docx(self._docx_bytes())

        self.assertEqual(extracted.title, 'Research Plan')
        self.assertIn('Local research agents can read Word documents.', extracted.text)
        self.assertIn('Table:', extracted.text)
        self.assertIn('Plan | Limit', extracted.text)
        self.assertEqual(extracted.metadata['document_type'], 'docx')
        self.assertEqual(extracted.metadata['paragraph_count'], 1)
        self.assertEqual(extracted.metadata['table_count'], 1)
        self.assertEqual(extracted.metadata['author'], 'Research Bot')

    def test_clean_pdf_page_text_preserves_table_like_columns(self) -> None:
        text = 'Plan      Price      Limit\nFree      $0         10 searches\n\nPro       $20        Unlimited'

        cleaned = clean_pdf_page_text(text)

        self.assertIn('Plan | Price | Limit', cleaned)
        self.assertIn('Free | $0 | 10 searches', cleaned)
        self.assertIn('Pro | $20 | Unlimited', cleaned)

    def test_extract_evidence_scores_query_blocks(self) -> None:
        text = 'Intro text.\n\nPricing includes online retrieval and citations.\n\nContact us.'

        evidence = extract_evidence(text, 'retrieval citations', source_id=2, url='https://example.com', title='Example')

        self.assertEqual(evidence[0]['source_id'], 2)
        self.assertIn('retrieval and citations', evidence[0]['quote'])
        self.assertEqual(evidence[0]['citation'].split('[')[0], 'source:2')


if __name__ == '__main__':
    unittest.main()
