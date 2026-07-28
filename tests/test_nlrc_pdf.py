import unittest
from unittest.mock import Mock, patch

from scripts.utils.nlrc_pdf import (
    _call_extract_text,
    extract_attachment_text,
    extract_pdf_attachments,
    pdf_retry_needed,
)


DETAIL_HTML = """
<ul>
  <li>
    <a
      data-cl-cd="65"
      data-file-id="65_65_13896"
      data-key="1"
      data-nlrc-event="click-download"
      title="사례5.pdf"
      href="javascript:;"
    >사례5.pdf</a>
  </li>
</ul>
"""

HWP_DETAIL_HTML = """
<a
  data-cl-cd="66"
  data-file-id="66_66_1"
  data-key="1"
  data-nlrc-event="click-download"
  title="조정사례.hwp"
>조정사례.hwp</a>
"""

MULTI_ATTACHMENT_HTML = DETAIL_HTML + HWP_DETAIL_HTML


class NlrcPdfTest(unittest.TestCase):
    def test_extracts_official_pdf_attachment_identifiers(self):
        attachments = extract_pdf_attachments(DETAIL_HTML)

        self.assertEqual(
            attachments,
            [
                {
                    "name": "사례5.pdf",
                    "file_id": "65_65_13896",
                    "key": "1",
                    "classification_code": "65",
                    "download_url": (
                        "https://nlrc.go.kr/nlrc/cmmn/file/download.do"
                        "?fileCors=&lgcfNm=&key=1&fileId=65_65_13896"
                    ),
                }
            ],
        )

    def test_extracts_hwp_attachment_identifiers(self):
        attachments = extract_pdf_attachments(HWP_DETAIL_HTML)

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["name"], "조정사례.hwp")
        self.assertEqual(attachments[0]["file_id"], "66_66_1")

    @patch("scripts.utils.nlrc_pdf._call_extract_text")
    @patch("scripts.utils.nlrc_pdf._download_pdf")
    def test_returns_full_text_and_provenance(
        self,
        download_pdf,
        call_extract_text,
    ):
        download_pdf.return_value = b"%PDF"
        call_extract_text.return_value = {
            "is_success": True,
            "full_text": "PDF 전체 본문",
            "page_count": 9,
            "is_searchable": False,
            "cost_usd": 0.12,
        }

        result = extract_attachment_text(
            DETAIL_HTML,
            "https://nlrc.go.kr/nlrc/mainCase/judgment/detail.do"
            "?jgmtSn=13896&jgmtDcsnSeCd=65",
        )

        self.assertTrue(result["success"])
        self.assertIn("PDF 전체 본문", result["text"])
        self.assertEqual(result["content_source"], "pdf_ocr")
        self.assertEqual(result["page_count"], 9)
        self.assertEqual(result["attachment"]["file_id"], "65_65_13896")

    @patch("scripts.utils.nlrc_pdf._call_extract_text")
    @patch("scripts.utils.nlrc_pdf._download_pdf")
    def test_returns_hwp_text_and_provenance(
        self,
        download_attachment,
        call_extract_text,
    ):
        download_attachment.return_value = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        call_extract_text.return_value = {
            "is_success": True,
            "full_text": "HWP 전체 본문",
            "file_type": "hwp",
            "page_count": None,
            "is_searchable": True,
            "cost_usd": 0.0,
        }

        result = extract_attachment_text(
            HWP_DETAIL_HTML,
            "https://nlrc.go.kr/nlrc/mainCase/mediatioin/detail.do"
            "?jgmtSn=1&jgmtDcsnSeCd=66",
        )

        self.assertTrue(result["success"])
        self.assertIn("HWP 전체 본문", result["text"])
        self.assertEqual(result["content_source"], "hwp_text")
        self.assertEqual(result["attachment"]["file_id"], "66_66_1")

    @patch("scripts.utils.nlrc_pdf._call_extract_text")
    @patch("scripts.utils.nlrc_pdf._download_pdf")
    def test_extracts_all_supported_attachments(
        self,
        download_attachment,
        call_extract_text,
    ):
        download_attachment.side_effect = [
            b"%PDF",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        ]
        call_extract_text.side_effect = [
            {
                "is_success": True,
                "full_text": "첫 번째 본문",
                "file_type": "pdf",
                "page_count": 2,
                "is_searchable": True,
                "cost_usd": 0.0,
            },
            {
                "is_success": True,
                "full_text": "두 번째 본문",
                "file_type": "hwp",
                "page_count": None,
                "is_searchable": True,
                "cost_usd": 0.0,
            },
        ]

        result = extract_attachment_text(
            MULTI_ATTACHMENT_HTML,
            "https://nlrc.go.kr/detail",
        )

        self.assertTrue(result["success"])
        self.assertIn("첫 번째 본문", result["text"])
        self.assertIn("두 번째 본문", result["text"])
        self.assertEqual(len(result["attachments"]), 2)
        self.assertEqual(result["content_source"], "mixed_attachments")
        self.assertFalse(pdf_retry_needed(result))

    def test_missing_pdf_returns_html_fallback_result(self):
        result = extract_attachment_text(
            "<html><body>첨부 없음</body></html>",
            "https://nlrc.go.kr/detail",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["content_source"], "html_fallback")
        self.assertEqual(result["error"], "Supported attachment not found")
        self.assertFalse(pdf_retry_needed(result))

    def test_pdf_extraction_failure_needs_retry(self):
        result = {
            "success": False,
            "attachment": {"file_id": "65_65_13896"},
        }

        self.assertTrue(pdf_retry_needed(result))

    def test_successful_pdf_extraction_does_not_need_retry(self):
        result = {
            "success": True,
            "attachment": {"file_id": "65_65_13896"},
        }

        self.assertFalse(pdf_retry_needed(result))

    @patch("scripts.utils.nlrc_pdf.http_requests.post")
    def test_extract_api_uses_standard_multipart_upload(self, post):
        response = Mock()
        response.json.return_value = {"is_success": True, "full_text": "본문"}
        response.raise_for_status.return_value = None
        post.return_value = response

        result = _call_extract_text(
            b"%PDF",
            "첨부.pdf",
            base_url="http://chat-generation:8000",
        )

        self.assertTrue(result["is_success"])
        post.assert_called_once()
        self.assertEqual(
            post.call_args.kwargs["files"]["file"],
            ("첨부.pdf", b"%PDF", "application/pdf"),
        )

    @patch("scripts.utils.nlrc_pdf._download_pdf", side_effect=RuntimeError("timeout"))
    def test_download_failure_returns_html_fallback_result(self, _download_pdf):
        result = extract_attachment_text(
            DETAIL_HTML,
            "https://nlrc.go.kr/detail",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["content_source"], "html_fallback")
        self.assertIn("timeout", result["error"])


if __name__ == "__main__":
    unittest.main()
