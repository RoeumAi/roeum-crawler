import unittest
from unittest.mock import patch

from scripts.case.logic.list_scraper import _post_list_page_with_retry


class PostListPageWithRetryTest(unittest.TestCase):
    @patch("scripts.case.logic.list_scraper.time.sleep")
    @patch("scripts.case.logic.list_scraper._post_list_page")
    def test_retries_on_failure_then_succeeds(self, mock_post, mock_sleep):
        mock_post.side_effect = [ConnectionError("RemoteDisconnected"), "soup"]

        result = _post_list_page_with_retry("1234", 1)

        self.assertEqual(result, "soup")
        self.assertEqual(mock_post.call_count, 2)

    @patch("scripts.case.logic.list_scraper.time.sleep")
    @patch("scripts.case.logic.list_scraper._post_list_page")
    def test_raises_after_max_retries(self, mock_post, mock_sleep):
        mock_post.side_effect = ConnectionError("RemoteDisconnected")

        with self.assertRaises(ConnectionError):
            _post_list_page_with_retry("1234", 1)

        self.assertEqual(mock_post.call_count, 3)


if __name__ == "__main__":
    unittest.main()
