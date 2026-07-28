import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts.case.logic import scraper as case_scraper
from scripts.legislation_expc.logic import scraper as legislation_scraper


def response_with_html(html: str) -> Mock:
    response = Mock()
    response.text = html
    response.raise_for_status.return_value = None
    return response


class ScraperSaveFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_case_reports_failed_when_mongodb_save_fails(self):
        html = '<div id="contentBody"><h2>판례 제목</h2><div id="conScroll"></div></div>'
        chunks = [{"title": "판시사항", "text": "내용", "metadata": {}}]

        with (
            patch.object(case_scraper.requests, "get", return_value=response_with_html(html)),
            patch.object(case_scraper, "parse_case_law_content", return_value=chunks),
            patch.object(
                case_scraper,
                "save_case_chunks_to_mongodb_async",
                new=AsyncMock(return_value=False),
            ),
        ):
            result = await case_scraper.scrape_and_save(
                "https://www.law.go.kr/LSW/precInfoP.do?precSeq=622053",
                output_dir="",
                output_name="test",
                save_jsonl=False,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["doc_id"], "622053")

    async def test_legislation_reports_failed_when_mongodb_save_fails(self):
        html = '<div id="contentBody"><h2>해석례 제목</h2><div id="conScroll"></div></div>'
        chunks = [{"title": "질의요지", "text": "내용", "metadata": {}}]

        with (
            patch.object(
                legislation_scraper.requests,
                "get",
                return_value=response_with_html(html),
            ),
            patch.object(legislation_scraper, "parse_content", return_value=chunks),
            patch.object(legislation_scraper, "save_to_mongodb", return_value=False),
        ):
            result = await legislation_scraper.scrape_and_save(
                "https://www.law.go.kr/LSW/expcInfoP.do?expcSeq=343515",
                save_jsonl=False,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["doc_id"], "343515")


if __name__ == "__main__":
    unittest.main()
