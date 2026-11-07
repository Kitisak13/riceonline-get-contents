# tests/test_pipeline.py - Offline Unit Tests for Rice Online News Pipeline
# =========================================================================

import os
import unittest
from datetime import datetime

from docx_generator import create_report_document
from raw_headline import clean_headline_text, extract_headlines_from_html
from utils import is_blocked_domain, is_valid_article_url, parse_date_flexible


class TestRiceNewsPipeline(unittest.TestCase):

    def test_parse_date_flexible(self):
        """Verify flexible parsing of different date formats."""
        d1 = parse_date_flexible("Friday, November 06, 2026")
        self.assertIsNotNone(d1)
        self.assertEqual(d1.strftime("%Y-%m-%d"), "2026-11-06")

        d2 = parse_date_flexible("2026-11-06")
        self.assertIsNotNone(d2)
        self.assertEqual(d2.strftime("%Y-%m-%d"), "2026-11-06")

        d3 = parse_date_flexible("November 6, 2026")
        self.assertIsNotNone(d3)
        self.assertEqual(d3.strftime("%Y-%m-%d"), "2026-11-06")

        # Invalid date
        self.assertIsNone(parse_date_flexible("Not a date string"))

    def test_clean_headline_text(self):
        """Verify HTML unescaping and whitespace normalization."""
        raw = "&quot;Thailand &amp; Vietnam rice exports surge&quot; &nbsp;"
        cleaned = clean_headline_text(raw)
        self.assertEqual(cleaned, '"Thailand & Vietnam rice exports surge"')

    def test_is_valid_article_url(self):
        """Verify SKILL.md domain exclusion and sub-path requirements."""
        # Valid news URLs
        self.assertTrue(is_valid_article_url("https://www.reuters.com/markets/commodities/rice-prices-2026-11-06/"))
        self.assertTrue(is_valid_article_url("https://asianews.network/thai-rice-exports-jump-2026/"))

        # Root / homepages should be rejected
        self.assertFalse(is_valid_article_url("https://www.reuters.com/"))
        self.assertFalse(is_valid_article_url("https://example.com"))

        # Blocked domains should be rejected
        self.assertFalse(is_valid_article_url("https://www.facebook.com/posts/12345"))
        self.assertFalse(is_valid_article_url("https://twitter.com/user/status/12345"))
        self.assertFalse(is_valid_article_url("https://riceonline.com/news/article-1"))
        self.assertFalse(is_valid_article_url("https://www.who.int/news/item/12345"))

        # Blocked extensions should be rejected
        self.assertFalse(is_valid_article_url("https://example.com/reports/rice_analysis.pdf"))

    def test_extract_headlines_from_html(self):
        """Verify HTML accordion parsing and colon stripping."""
        mock_html = """
        <div class="accordionButton">Friday, November 06, 2026</div>
        <div class="accordionContent">
            <a class="free_article_subscribers">USA RICE: &quot;U.S. Rice Industry Will Benefit&quot;</a>
            <a class="free_article_subscribers">AMP: : &quot;Arkansas Rice Leaders Meet Boozman&quot;</a>
        </div>
        """
        results = extract_headlines_from_html(mock_html, cutoff_date_str="2026-11-05")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["source"], "USA RICE")
        self.assertEqual(results[0]["headline"], "U.S. Rice Industry Will Benefit")
        self.assertEqual(results[1]["source"], "AMP")
        # Leading colon must be stripped
        self.assertEqual(results[1]["headline"], "Arkansas Rice Leaders Meet Boozman")

    def test_create_report_document(self):
        """Verify document creation with valid typography and hyperlinks."""
        test_articles = [
            {
                "headline": "Test Headline",
                "source": "Test Source",
                "date": "2026-11-06",
                "URL": "https://example.com/news/rice-article",
                "content": "This is a test article body paragraph."
            }
        ]
        # Test default filename format: [YYYY-MM-DD] Rice News Report.docx
        output_path = create_report_document(test_articles)
        self.assertTrue(os.path.exists(output_path))
        self.assertTrue(os.path.basename(output_path).startswith("[2026-11-06] Rice News Report"))
        self.assertGreater(os.path.getsize(output_path), 0)

        # Cleanup
        if os.path.exists(output_path):
            os.remove(output_path)

    def test_upload_report_to_drive_skip_without_url(self):
        """Verify drive uploader skips gracefully when webhook URL is missing."""
        from drive_uploader import upload_report_to_drive
        result = upload_report_to_drive("non_existent_file.docx", webhook_url="")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

