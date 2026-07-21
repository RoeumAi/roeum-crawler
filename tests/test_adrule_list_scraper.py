import unittest
from unittest.mock import patch

from scripts.adrule.logic.list_scraper import _fetch_page


class IsUpcomingComputationTest(unittest.TestCase):
    @patch("scripts.adrule.logic.list_scraper._fetch_page")
    @patch("scripts.adrule.logic.list_scraper._total_pages")
    @patch("scripts.adrule.logic.list_scraper.date")
    def test_marks_future_effective_date_as_upcoming(self, mock_date, mock_total_pages, mock_fetch_page):
        import asyncio
        from scripts.adrule.logic.list_scraper import fetch_urls

        mock_date.today.return_value.strftime.return_value = "20260721"
        mock_total_pages.return_value = 1
        mock_fetch_page.return_value = [
            {"행정규칙일련번호": "111", "시행일자": "20260101", "행정규칙명": "과거 시행 규칙", "소관부처명": "고용노동부"},
            {"행정규칙일련번호": "222", "시행일자": "20261231", "행정규칙명": "미래 시행 규칙", "소관부처명": "고용노동부"},
        ]

        items = asyncio.run(fetch_urls("https://example.com", max_pages_arg=1))

        by_seq = {item["url"]: item for item in items}
        past_item = by_seq["https://www.law.go.kr/admRulInfoP.do?admRulSeq=111"]
        future_item = by_seq["https://www.law.go.kr/admRulInfoP.do?admRulSeq=222"]

        self.assertFalse(past_item["is_upcoming"])
        self.assertTrue(future_item["is_upcoming"])


if __name__ == "__main__":
    unittest.main()
