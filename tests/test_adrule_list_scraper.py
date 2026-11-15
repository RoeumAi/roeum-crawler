import unittest
from unittest.mock import patch

from scripts.adrule.logic import list_scraper
from scripts.core import lawgokr_openapi as api
from tests.test_lawgokr_openapi import FakeGet, no_retry


class AdruleListTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(api, "call_with_retry", no_retry)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("scripts.adrule.logic.list_scraper.date")
    def test_marks_future_effective_date_as_upcoming_and_keeps_moel_only(self, mock_date):
        mock_date.today.return_value.strftime.return_value = "20260721"
        rows = [
            {"행정규칙일련번호": "111", "시행일자": "20260101", "행정규칙명": "과거 시행 규칙", "소관부처명": "고용노동부"},
            {"행정규칙일련번호": "222", "시행일자": "20261231", "행정규칙명": "미래 시행 규칙", "소관부처명": "중앙노동위원회"},
            {"행정규칙일련번호": "333", "시행일자": "20260101", "행정규칙명": "다른 부처", "소관부처명": "환경부"},
        ]
        get = FakeGet(lambda u, p: {"AdmRulSearch": {"totalCnt": "3", "admrul": rows}})
        items = list_scraper.collect_items(get=get)

        by_url = {item["url"]: item for item in items}
        self.assertEqual(sorted(by_url), ["https://www.law.go.kr/admRulInfoP.do?admRulSeq=111",
                                          "https://www.law.go.kr/admRulInfoP.do?admRulSeq=222"])
        self.assertFalse(by_url["https://www.law.go.kr/admRulInfoP.do?admRulSeq=111"]["is_upcoming"])
        self.assertTrue(by_url["https://www.law.go.kr/admRulInfoP.do?admRulSeq=222"]["is_upcoming"])
        self.assertEqual(get.calls[0][1]["target"], "admrul")


if __name__ == "__main__":
    unittest.main()
