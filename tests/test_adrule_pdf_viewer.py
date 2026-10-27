"""행정규칙 PDF 뷰어 문서: 화면 OCR 대신 원본 파일을 받아 chat_generation 으로 파싱한다."""
import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts.adrule.logic import scraper as adrule

VIEWER_HTML = ('<iframe class="fancybox-iframe" src="/LSW/viewer/skin/doc.html?imageConverting=true&amp;key=155278071'
               '&amp;contextPath=/viewer/DATA/ADMRUL/1/SKIN/155278071"></iframe>')


def page_with(html):
    page = Mock()
    page.content = AsyncMock(return_value=html)
    return page


class ViewerFileKeysTest(unittest.TestCase):
    def test_extracts_file_key_from_viewer_iframe(self):
        self.assertEqual(adrule.viewer_file_keys(VIEWER_HTML), ["155278071"])

    def test_no_viewer_means_no_keys(self):
        self.assertEqual(adrule.viewer_file_keys("<div>본문</div>"), [])


class PdfViewerContentTest(unittest.IsolatedAsyncioTestCase):
    async def test_original_file_is_parsed_with_tables_kept(self):
        extracted = {"structured_text": "# 최저임금 고시\n\n| 업종 | 시간급 |\n|---|---|\n| 모든 산업 | 10,700원 |",
                     "extraction_status": "complete"}
        with patch.object(adrule, "_download_and_extract", return_value=extracted) as download:
            chunks = await adrule._parse_pdf_viewer_content(page_with(VIEWER_HTML), "2100000283564", "u", "최저임금 고시", "o")
        download.assert_called_once_with("155278071")
        self.assertEqual(len(chunks), 1)
        self.assertIn("| 모든 산업 | 10,700원 |", chunks[0]["text"])
        self.assertEqual(chunks[0]["metadata"]["source_type"], "pdf")

    async def test_parse_failure_raises_instead_of_saving_error_text(self):
        # 예전에는 OCR 실패 시 "[OCR 처리 오류]" 가 본문으로 저장됐다(최저임금 고시 등 4건).
        with patch.object(adrule, "_download_and_extract", side_effect=ValueError("no usable text")):
            with self.assertRaises(ValueError):
                await adrule._parse_pdf_viewer_content(page_with(VIEWER_HTML), "d", "u", "t", "o")

    async def test_missing_key_raises(self):
        with self.assertRaises(ValueError):
            await adrule._parse_pdf_viewer_content(page_with('<iframe class="fancybox-iframe" src="/x"></iframe>'),
                                                   "d", "u", "t", "o")


if __name__ == "__main__":
    unittest.main()
