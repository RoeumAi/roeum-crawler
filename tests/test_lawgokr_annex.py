"""별표: Open API 응답에서 별표 원본 파일을 고르고, 파싱한 표를 조문 행으로 만든다."""
import unittest
from unittest.mock import Mock, patch


from scripts.core import lawgokr_annex as annex

LAW_RECORD = {"법령": {"별표": {"별표단위": [
    {"별표번호": "0001", "별표가지번호": "00", "별표구분": "별표", "별표제목": "과태료의 부과기준(제22조 관련)",
     "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=1", "별표서식파일링크": "/LSW/flDownload.do?flSeq=2",
     "별표PDF파일명": "a.pdf"},
    {"별표번호": "0001", "별표가지번호": "02", "별표구분": "별표", "별표제목": "가지 별표",
     "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=3", "별표PDF파일명": "b.pdf"},
    {"별표번호": "0002", "별표구분": "서식", "별표제목": "신청서", "별표서식PDF파일링크": "/x"},
]}}}

ADRULE_RECORD = {"AdmRulService": {"첨부파일": {
    "첨부파일링크": ["http://law.go.kr/flDownload.do?flSeq=10", "http://law.go.kr/flDownload.do?flSeq=11",
                 "http://law.go.kr/flDownload.do?flSeq=12", "http://law.go.kr/flDownload.do?flSeq=13"],
    "첨부파일명": ["[별표 1] 요율표.hwpx", "[별표 1] 요율표.pdf", "최저임금 고시 재개정 이유서.pdf", "고시 본문.pdf"],
}}}


class AnnexListTest(unittest.TestCase):
    def test_law_annexes_skip_forms_and_prefer_pdf(self):
        found = annex.law_annexes(LAW_RECORD)
        self.assertEqual([a.article_number for a in found], ["별표1", "별표1의2"])
        self.assertEqual(found[0].url, "https://www.law.go.kr/LSW/flDownload.do?flSeq=1")
        self.assertEqual(found[0].title, "[별표 1] 과태료의 부과기준(제22조 관련)")

    def test_adrule_annexes_only_annex_files_one_per_name(self):
        found = annex.adrule_annexes(ADRULE_RECORD)
        self.assertEqual([(a.article_number, a.file_name) for a in found], [("별표1", "[별표 1] 요율표.pdf")])
        self.assertTrue(found[0].url.startswith("https://"))

    def test_adrule_annex_section_is_used_first_and_text_only_unit_kept(self):
        record = {"AdmRulService": {"별표": {"별표단위": [
            {"별표번호": "0001", "별표제목": "선정 기준", "별표내용": [["┏━┓", "┃기준┃"]]},
            {"별표번호": "0002", "별표제목": "검사항목", "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=9"},
        ]}, "첨부파일": ADRULE_RECORD["AdmRulService"]["첨부파일"]}}
        found = annex.adrule_annexes(record)
        self.assertEqual([a.article_number for a in found], ["별표1", "별표2"])
        self.assertEqual((found[0].url, found[0].text), ("", "┏━┓\n┃기준┃"))
        rows = annex.annex_articles(found[:1])
        self.assertEqual(rows[0]["content"], "┏━┓\n┃기준┃")
        self.assertEqual(rows[0]["annex"]["pdf_extraction_status"], "api_text")

    def test_duplicate_numbers_get_suffix(self):
        units = [{"별표번호": "0000", "별표제목": "가", "별표서식PDF파일링크": "/a"},
                 {"별표번호": "0000", "별표제목": "나", "별표서식PDF파일링크": "/b"}]
        self.assertEqual([a.article_number for a in annex.annexes_from_units(units)], ["별표", "별표-2"])

    def test_no_annex_section(self):
        self.assertEqual(annex.law_annexes({"법령": {}}), [])
        self.assertEqual(annex.adrule_annexes({}), [])


class CollectTest(unittest.TestCase):
    def test_tables_become_rows(self):
        parsed = {"is_success": True, "extraction_status": "complete", "structured_text": "| 위반 | 과태료 |\n|---|---|\n| 가 | 100만원 |",
                  "full_text": "x", "document_blocks": [{"kind": "table", "cells": []}],
                  "extraction_metadata": {"paragraph_projection": {"status": "complete"}}}
        with patch.object(annex.requests, "get", return_value=Mock(content=b"%PDF", raise_for_status=Mock())):
            rows, failed = annex.collect_annex_articles("law", LAW_RECORD, Mock(), extract=lambda b, n: parsed)
        self.assertEqual(failed, 0)
        self.assertEqual(rows[0]["article_number"], "별표1")
        self.assertIn("| 가 | 100만원 |", rows[0]["content"])
        meta = annex.annex_metadata(rows[0])
        self.assertEqual(meta["is_annex"], True)
        self.assertEqual(meta["document_blocks"][0]["kind"], "table")  # 화면이 표로 그리도록
        self.assertNotIn("document_blocks_scope", meta)

    def test_api_failure_or_parse_failure_counts_as_failed(self):
        self.assertEqual(annex.collect_annex_articles("law", None, Mock()), ([], 1))
        with patch.object(annex.requests, "get", side_effect=RuntimeError("down")):
            self.assertEqual(annex.collect_annex_articles("law", LAW_RECORD, Mock()), ([], 1))


class PartialAnnexTest(unittest.TestCase):
    def test_partial_annex_is_success_with_persisted_quality_warning(self):
        # Availability and completeness are independent; keep unread pages as metadata.
        from scripts.core.run_report import EXTRACT_PARTIAL, start_run_report
        parsed = {"is_success": True, "extraction_status": "partial", "structured_text": "읽은 쪽", "full_text": "읽은 쪽",
                  "extraction_metadata": {"failed_page_numbers": [3, 14]}}
        report = start_run_report("law")
        with patch.object(annex.requests, "get", return_value=Mock(content=b"%PDF", raise_for_status=Mock())):
            rows, failed = annex.collect_annex_articles("law", LAW_RECORD, Mock(), extract=lambda b, n: parsed,
                                                        doc_id="289523", title="국가기술자격법 시행규칙")
        self.assertEqual(failed, 0)
        self.assertEqual([r["content"] for r in rows], ["읽은 쪽", "읽은 쪽"])
        self.assertFalse(report.reason_counts.get(EXTRACT_PARTIAL))
        self.assertNotIn("law:doc:289523", report.document_details)
        self.assertEqual(annex.annex_metadata(rows[0])["unread_page_numbers"], [3, 14])
        self.assertEqual(annex.annex_metadata(rows[0])["pdf_extraction_status"], "partial")

    def test_title_comes_from_record_when_not_given(self):
        from scripts.core.run_report import start_run_report
        report = start_run_report("adrule")
        annex.collect_annex_articles("adrule", None, Mock(), doc_id="21")
        self.assertEqual(report.document_details["adrule:doc:21"]["detail"], "별표 목록 조회 실패")
        self.assertEqual(annex.record_title("law", {"법령": {"기본정보": {"법령명_한글": "근로기준법"}}}), "근로기준법")
        self.assertEqual(annex.record_title("adrule", {"AdmRulService": {"행정규칙기본정보": {"행정규칙명": "고시"}}}), "고시")


class RetainedAnnexTest(unittest.TestCase):
    def test_one_empty_annex_does_not_discard_other_successes(self):
        parsed = {"is_success": True, "full_text": "보존할 본문", "extraction_status": "complete"}
        with patch.object(annex.requests, "get", return_value=Mock(content=b"pdf", raise_for_status=Mock())):
            rows, failed = annex.collect_annex_articles("law", LAW_RECORD, Mock(),
                extract=Mock(side_effect=[parsed, {"is_success": False, "full_text": ""}]))
        self.assertEqual(failed, 1)
        self.assertEqual([r['content'] for r in rows], ['보존할 본문'])


class PausedExtractTest(unittest.TestCase):
    def test_paused_server_counts_as_failed_with_reason(self):
        from scripts.core.run_report import EXTRACT_SERVER_DOWN, start_run_report
        from scripts.utils.document_extraction import ExtractServerUnavailable
        report = start_run_report("adrule")
        with patch.object(annex.requests, "get", return_value=Mock(content=b"%PDF", raise_for_status=Mock())):
            rows = annex.collect_annex_articles("law", LAW_RECORD, Mock(),
                                                extract=Mock(side_effect=ExtractServerUnavailable("x")))
        self.assertEqual(rows, ([], 1))
        self.assertEqual(report.reason_counts[EXTRACT_SERVER_DOWN], 1)


if __name__ == "__main__":
    unittest.main()


class AnnexDiffTest(unittest.TestCase):
    def test_annexes_are_not_new_when_old_version_predates_annex_collection(self):
        from scripts.core.flows.refresh_current_status_flow import comparable_articles
        old = [{"article_number": "1", "content": "가"}]
        new = [{"article_number": "1", "content": "가"}, {"article_number": "별표1", "content": "| a |"}]
        self.assertEqual(comparable_articles(old, new), (old, [new[0]]))
        old_with = old + [{"article_number": "별표1", "content": "| b |"}]
        self.assertEqual(comparable_articles(old_with, new), (old_with, new))
