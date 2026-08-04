import unittest

from crawl import summarize_scrape_results


class SummarizeScrapeResultsTest(unittest.TestCase):
    def test_no_change_counted_separately_from_success(self):
        results = [
            {"status": "success", "action": "new_version"},
            {"status": "success", "action": "update_existing"},
            {"status": "success", "action": "no_change"},
            {"status": "success", "action": "no_change"},
            {"status": "failed", "error": "boom"},
        ]

        success, no_change, failed = summarize_scrape_results(results)

        self.assertEqual(success, 2)
        self.assertEqual(no_change, 2)
        self.assertEqual(failed, 1)

    def test_action_without_recognized_value_counts_as_success(self):
        results = [
            {"status": "success", "action": "insert"},
            {"status": "success"},
        ]

        success, no_change, failed = summarize_scrape_results(results)

        self.assertEqual(success, 2)
        self.assertEqual(no_change, 0)
        self.assertEqual(failed, 0)

    def test_exception_results_are_ignored(self):
        results = [
            {"status": "success", "action": "new_version"},
            ValueError("gather returned an exception"),
        ]

        success, no_change, failed = summarize_scrape_results(results)

        self.assertEqual(success, 1)
        self.assertEqual(no_change, 0)
        self.assertEqual(failed, 0)


if __name__ == "__main__":
    unittest.main()
