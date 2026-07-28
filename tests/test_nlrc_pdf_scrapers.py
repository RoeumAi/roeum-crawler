import unittest
from unittest.mock import patch

from scripts.judgment.logic import scraper as judgment_scraper
from scripts.mediation_case.logic import scraper as mediation_scraper


JUDGMENT_HTML = """
<table class="BD_table">
  <tr><th class="title">판정 제목</th></tr>
  <tr><th>자료구분</th><td>부당해고</td></tr>
  <tr><th>판정사항</th><td>판정사항 요약</td></tr>
  <tr><th>판정요지</th><td>판정요지 요약</td></tr>
  <tr><th>등록일</th><td>2026-07-28</td></tr>
</table>
"""

MEDIATION_HTML = """
<table class="BD_table">
  <tr><th class="title">조정 제목</th></tr>
  <tr><th>자료구분</th><td>조정</td></tr>
  <tr><th>요약내용</th><td>조정 요약</td></tr>
  <tr><th>등록일</th><td>2026-07-28</td></tr>
</table>
"""


def extracted_pdf(text: str) -> dict:
    return {
        "success": True,
        "text": text,
        "content_source": "pdf_ocr",
        "attachment": {
            "name": "첨부.pdf",
            "file_id": "65_65_1",
            "download_url": "https://nlrc.go.kr/download.pdf",
        },
        "page_count": 3,
        "is_searchable": False,
        "cost_usd": 0.05,
        "error": "",
    }

def extracted_hwp(text: str) -> dict:
    result = extracted_pdf(text)
    result["content_source"] = "hwp_text"
    result["attachment"] = {
        "name": "첨부.hwp",
        "file_id": "66_66_1",
        "download_url": "https://nlrc.go.kr/download.hwp",
    }
    result["page_count"] = None
    result["is_searchable"] = True
    result["cost_usd"] = 0.0
    return result


class NlrcPdfScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_judgment_stores_pdf_full_text_and_attachment_metadata(self):
        captured = {}

        def save(document):
            captured.update(document)
            return True

        with (
            patch.object(judgment_scraper, "_fetch_detail_html", return_value=JUDGMENT_HTML),
            patch.object(
                judgment_scraper,
                "extract_attachment_text",
                return_value=extracted_pdf("판정 PDF 전체 본문"),
            ),
            patch.object(judgment_scraper, "save_judgment_to_mongodb", side_effect=save),
        ):
            result = await judgment_scraper.scrape_and_save(
                "https://nlrc.go.kr/nlrc/mainCase/judgment/detail.do"
                "?jgmtSn=1&jgmtDcsnSeCd=65",
                output_dir="",
                output_name="test",
                save_jsonl=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertIn("판정 PDF 전체 본문", captured["content"])
        self.assertIn("판정요지 요약", captured["content"])
        self.assertEqual(captured["metadata"]["content_source"], "pdf_ocr")
        self.assertEqual(captured["metadata"]["attachment_file_id"], "65_65_1")
        self.assertEqual(captured["metadata"]["pdf_page_count"], 3)
        self.assertFalse(captured["metadata"]["pdf_retry_needed"])

    async def test_mediation_stores_pdf_full_text_and_attachment_metadata(self):
        captured = {}

        def save(document):
            captured.update(document)
            return True

        with (
            patch.object(mediation_scraper, "_fetch_detail_html", return_value=MEDIATION_HTML),
            patch.object(
                mediation_scraper,
                "extract_attachment_text",
                return_value=extracted_pdf("조정 PDF 전체 본문"),
            ),
            patch.object(mediation_scraper, "save_mediation_to_mongodb", side_effect=save),
        ):
            result = await mediation_scraper.scrape_and_save(
                "https://nlrc.go.kr/nlrc/mainCase/mediatioin/detail.do"
                "?jgmtSn=1&jgmtDcsnSeCd=66",
                output_dir="",
                output_name="test",
                save_jsonl=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertIn("조정 PDF 전체 본문", captured["content"])
        self.assertIn("조정 요약", captured["content"])
        self.assertEqual(captured["metadata"]["content_source"], "pdf_ocr")
        self.assertEqual(captured["metadata"]["attachment_name"], "첨부.pdf")
        self.assertFalse(captured["metadata"]["pdf_retry_needed"])

    async def test_mediation_stores_hwp_full_text_and_attachment_metadata(self):
        captured = {}

        with (
            patch.object(mediation_scraper, "_fetch_detail_html", return_value=MEDIATION_HTML),
            patch.object(
                mediation_scraper,
                "extract_attachment_text",
                return_value=extracted_hwp("조정 HWP 전체 본문"),
            ),
            patch.object(
                mediation_scraper,
                "save_mediation_to_mongodb",
                side_effect=lambda document: captured.update(document) or True,
            ),
        ):
            result = await mediation_scraper.scrape_and_save(
                "https://nlrc.go.kr/nlrc/mainCase/mediatioin/detail.do"
                "?jgmtSn=1&jgmtDcsnSeCd=66",
                output_dir="",
                output_name="test",
                save_jsonl=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertIn("조정 HWP 전체 본문", captured["content"])
        self.assertEqual(captured["metadata"]["content_source"], "hwp_text")
        self.assertEqual(captured["metadata"]["attachment_name"], "첨부.hwp")

    async def test_judgment_keeps_html_content_when_pdf_extraction_fails(self):
        captured = {}
        fallback = {
            "success": False,
            "text": "",
            "content_source": "html_fallback",
            "attachment": None,
            "page_count": None,
            "is_searchable": None,
            "cost_usd": 0.0,
            "error": "Supported attachment not found",
        }

        with (
            patch.object(judgment_scraper, "_fetch_detail_html", return_value=JUDGMENT_HTML),
            patch.object(judgment_scraper, "extract_attachment_text", return_value=fallback),
            patch.object(
                judgment_scraper,
                "save_judgment_to_mongodb",
                side_effect=lambda document: captured.update(document) or True,
            ),
        ):
            result = await judgment_scraper.scrape_and_save(
                "https://nlrc.go.kr/nlrc/mainCase/judgment/detail.do"
                "?jgmtSn=1&jgmtDcsnSeCd=65",
                output_dir="",
                output_name="test",
                save_jsonl=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertIn("판정사항 요약", captured["content"])
        self.assertEqual(captured["metadata"]["content_source"], "html_fallback")
        self.assertIn("not found", captured["metadata"]["pdf_error"])
        self.assertFalse(captured["metadata"]["pdf_retry_needed"])

    async def test_judgment_marks_attached_pdf_failure_for_daily_retry(self):
        captured = {}
        fallback = {
            "success": False,
            "text": "",
            "content_source": "html_fallback",
            "attachment": {
                "name": "첨부.pdf",
                "file_id": "65_65_1",
                "download_url": "https://nlrc.go.kr/download.pdf",
            },
            "page_count": None,
            "is_searchable": None,
            "cost_usd": 0.0,
            "error": "extract API timeout",
        }

        with (
            patch.object(judgment_scraper, "_fetch_detail_html", return_value=JUDGMENT_HTML),
            patch.object(judgment_scraper, "extract_attachment_text", return_value=fallback),
            patch.object(
                judgment_scraper,
                "save_judgment_to_mongodb",
                side_effect=lambda document: captured.update(document) or True,
            ),
        ):
            await judgment_scraper.scrape_and_save(
                "https://nlrc.go.kr/nlrc/mainCase/judgment/detail.do"
                "?jgmtSn=1&jgmtDcsnSeCd=65",
                output_dir="",
                output_name="test",
                save_jsonl=False,
            )

        self.assertTrue(captured["metadata"]["pdf_retry_needed"])


if __name__ == "__main__":
    unittest.main()
