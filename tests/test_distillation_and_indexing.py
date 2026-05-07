"""Tests for HTML distillation and stealth mode."""

from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from web_research.extract import distill_html


class DistillationTests(unittest.TestCase):
    """Test HTML boilerplate removal (distillation layer)."""

    def test_distill_removes_nav_header_footer(self) -> None:
        """Verify distillation removes navigation, header, footer elements."""
        html = '''
        <html>
            <body>
                <header><nav>Navigation Menu</nav></header>
                <main>
                    <h1>Article Title</h1>
                    <p>Main content here.</p>
                </main>
                <footer>Copyright 2024</footer>
            </body>
        </html>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        distilled = distill_html(soup)
        
        # Header, nav, footer should be removed
        self.assertIsNone(distilled.find('header'))
        self.assertIsNone(distilled.find('nav'))
        self.assertIsNone(distilled.find('footer'))
        
        # Main content should remain
        self.assertIsNotNone(distilled.find('h1'))
        self.assertIsNotNone(distilled.find('p'))

    def test_distill_removes_ads_and_sponsored(self) -> None:
        """Verify distillation removes ads and sponsored content."""
        html = '''
        <html>
            <body>
                <main>
                    <p>Main article content.</p>
                    <div class="advertisement">Ad content here</div>
                    <p>More article content.</p>
                    <aside class="ads-sidebar">Sidebar ads</aside>
                </main>
            </body>
        </html>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        distilled = distill_html(soup)
        
        # Ads should be removed
        self.assertIsNone(distilled.find(class_='advertisement'))
        self.assertIsNone(distilled.find(class_='ads-sidebar'))
        
        # Main content preserved
        paragraphs = distilled.find_all('p')
        self.assertEqual(len(paragraphs), 2)

    def test_distill_removes_related_recommended(self) -> None:
        """Verify distillation removes related/recommended widget sections."""
        html = '''
        <html>
            <body>
                <article>
                    <p>Main content.</p>
                </article>
                <div class="related-articles">
                    <h3>Related</h3>
                    <ul><li>Link 1</li></ul>
                </div>
                <div class="recommended-for-you">
                    <h3>Recommended</h3>
                    <ul><li>Link 2</li></ul>
                </div>
            </body>
        </html>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        distilled = distill_html(soup)
        
        # Related/recommended should be removed
        self.assertIsNone(distilled.find(class_='related-articles'))
        self.assertIsNone(distilled.find(class_='recommended-for-you'))

    def test_distill_preserves_main_article_content(self) -> None:
        """Verify distillation preserves main article structure and content."""
        html = '''
        <html>
            <body>
                <main>
                    <article>
                        <h1>Article Title</h1>
                        <p>First paragraph with important information.</p>
                        <h2>Section 1</h2>
                        <p>Section content.</p>
                        <h2>Section 2</h2>
                        <p>More content.</p>
                    </article>
                </main>
            </body>
        </html>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        distilled = distill_html(soup)
        
        # All article content should be preserved
        self.assertIsNotNone(distilled.find('h1'))
        self.assertEqual(len(distilled.find_all('h2')), 2)
        self.assertEqual(len(distilled.find_all('p')), 3)


class StealthModeTests(unittest.TestCase):
    """Test browser stealth mode configuration."""

    def test_stealth_mode_config_default(self) -> None:
        """Verify stealth mode is enabled by default."""
        from web_research.config import settings
        # Default should be True
        self.assertTrue(settings.browser_stealth_mode)

    def test_stealth_mode_can_be_disabled(self) -> None:
        """Verify stealth mode can be disabled via environment variable."""
        import os
        original_value = os.environ.get('BROWSER_STEALTH_MODE')
        
        try:
            os.environ['BROWSER_STEALTH_MODE'] = 'false'
            # Reload config to pick up new env var
            import importlib
            import web_research.config
            importlib.reload(web_research.config)
            
            self.assertFalse(web_research.config.settings.browser_stealth_mode)
        finally:
            if original_value is None:
                os.environ.pop('BROWSER_STEALTH_MODE', None)
            else:
                os.environ['BROWSER_STEALTH_MODE'] = original_value
            # Reload again to restore
            import importlib
            import web_research.config
            importlib.reload(web_research.config)


if __name__ == '__main__':
    unittest.main()
