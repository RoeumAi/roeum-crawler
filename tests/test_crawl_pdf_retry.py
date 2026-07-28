import unittest

from crawl import get_crawled_doc_id_filter


class CrawledDocIdFilterTest(unittest.TestCase):
    def test_nlrc_pdf_collections_exclude_retryable_failures(self):
        expected = {"metadata.pdf_retry_needed": {"$ne": True}}

        self.assertEqual(get_crawled_doc_id_filter("judgment"), expected)
        self.assertEqual(get_crawled_doc_id_filter("mediation_case"), expected)

    def test_other_collections_keep_existing_behavior(self):
        self.assertEqual(get_crawled_doc_id_filter("case"), {})


if __name__ == "__main__":
    unittest.main()
