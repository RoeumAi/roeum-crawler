import unittest

from scripts.utils.pdf_text_cleaner import (
    clean_pdf_artifacts,
    is_stub_content,
)


class CleanHrdbFooterTest(unittest.TestCase):
    def test_removes_hrdb_print_footer_block(self):
        text = (
            "육아휴직기간을 호봉승급기간에서 제외해도 타당한지\n"
            "26. 7. 3. 오후 3:14\n"
            "hrdb.kr/search/print?wm_id=29286&pdf=1\n"
            "https://hrdb.kr/search/print?wm_id=29286&pdf=1\n"
            "1/1"
        )

        cleaned = clean_pdf_artifacts(text)

        self.assertEqual(cleaned, "육아휴직기간을 호봉승급기간에서 제외해도 타당한지")
        self.assertNotIn("hrdb", cleaned)

    def test_removes_hrdb_footer_with_www_variant(self):
        text = (
            "직위해제 및 대기명령 처분사유가 일부만 해당되는 경우\n"
            "26. 7. 1. 오후 10:41\n"
            "hrdb.kr/search/print?wm_id=23934&pdf=1\n"
            "https://www.hrdb.kr/search/print?wm_id=23934&pdf=1\n"
            "5/11\n"
            "\n"
            "- 두 번째 사유인 같은 규정 제47조"
        )

        cleaned = clean_pdf_artifacts(text)

        self.assertNotIn("hrdb", cleaned)
        self.assertNotIn("5/11", cleaned)
        self.assertIn("직위해제 및 대기명령", cleaned)
        self.assertIn("- 두 번째 사유인 같은 규정 제47조", cleaned)

    def test_removes_footer_embedded_mid_content(self):
        text = (
            "앞 문장.\n"
            "26. 7. 3. 오후 3:14\n"
            "https://hrdb.kr/search/print?wm_id=1&pdf=1\n"
            "2/3\n"
            "\n"
            "뒤 문장."
        )

        cleaned = clean_pdf_artifacts(text)

        self.assertIn("앞 문장.", cleaned)
        self.assertIn("뒤 문장.", cleaned)
        self.assertNotIn("hrdb", cleaned)
        self.assertNotIn("오후 3:14", cleaned)


class KeepLegitimateContentTest(unittest.TestCase):
    def test_keeps_inbody_dates_without_time(self):
        text = (
            "○○○○하우스는 2017. 9. 21. 원고와 공사기간을 정하였다. "
            "원고는 2017. 10. 30. 계약을 체결하였다."
        )

        self.assertEqual(clean_pdf_artifacts(text), text)

    def test_keeps_inbody_ohu_without_clock_time(self):
        # "오후경" (no HH:MM) is legitimate body text, not a print timestamp
        text = "근로자들과 2017. 11. 6. 오후경부터 잭서포트를 해체하는 작업을 수행하였다."

        self.assertEqual(clean_pdf_artifacts(text), text)

    def test_keeps_inline_source_citation(self):
        # parenthetical "(출처 : 화학용어사전)" is real legal text
        text = "유리소지에 대한 용어정리(출처 : 화학용어사전) - '소지'란 원료혼합물을 말한다."

        self.assertEqual(clean_pdf_artifacts(text), text)

    def test_clean_content_is_unchanged(self):
        text = (
            "업무수행 평가결과에 따른 인센티브, 기준물량 초과 시 분기마다"
            "지급하는 격려금의 임금성"
        )

        self.assertEqual(clean_pdf_artifacts(text), text)


class NlrcPageMarkerTest(unittest.TestCase):
    def test_removes_page_break_marker_line(self):
        text = (
            "안전사고가 발생할 수 있는 점이 고려되어야 한다.\n"
            "\n"
            "- 12 -\n"
            "\n"
            "하루 출입횟수 8회 제한을 계열사에 적용한 것은 과하지 않다."
        )

        cleaned = clean_pdf_artifacts(text)

        self.assertNotIn("- 12 -", cleaned)
        self.assertIn("고려되어야 한다.", cleaned)
        self.assertIn("하루 출입횟수 8회 제한을", cleaned)

    def test_keeps_dash_enclosed_words(self):
        # a real "- 단어 -" style emphasis must not be removed (only numeric markers)
        text = "이 사건 - 근로자 - 는 정당한 사유가 있었다."

        self.assertEqual(clean_pdf_artifacts(text), text)


class AttachmentMarkerTest(unittest.TestCase):
    def test_removes_attachment_filename_marker(self):
        text = (
            "판정요지: 부당노동행위에 해당하지 않는다.\n"
            "\n"
            "[첨부: 사례2.pdf]\n"
            "사례 2\n"
            "[2025. 12. 22. 판정 / 초심: 일부 인정]"
        )

        cleaned = clean_pdf_artifacts(text)

        self.assertNotIn("[첨부: 사례2.pdf]", cleaned)
        self.assertIn("판정요지: 부당노동행위에 해당하지 않는다.", cleaned)
        self.assertIn("사례 2", cleaned)

    def test_removes_attachment_marker_with_bracketed_filename(self):
        # NLRC filenames embed case numbers in brackets, e.g.
        # "재심판정서[중앙2018부해1095]-8.기타징계.pdf"
        text = (
            "부당하다고 판정한 사례\n"
            "\n"
            "[첨부파일 전문]\n"
            "[첨부: 재심판정서[중앙2018부해1095]-8.기타징계.pdf]\n"
            "판 정 서"
        )

        cleaned = clean_pdf_artifacts(text)

        self.assertNotIn("[첨부:", cleaned)
        self.assertNotIn(".pdf", cleaned)
        self.assertIn("[첨부파일 전문]", cleaned)  # section label kept
        self.assertIn("판 정 서", cleaned)


class WhitespaceTest(unittest.TestCase):
    def test_collapses_excess_blank_lines(self):
        text = "가.\n\n\n\n나."

        self.assertEqual(clean_pdf_artifacts(text), "가.\n\n나.")

    def test_idempotent(self):
        text = (
            "앞.\n26. 7. 3. 오후 3:14\nhttps://hrdb.kr/x\n1/1\n\n- 3 -\n\n뒤."
        )

        once = clean_pdf_artifacts(text)
        twice = clean_pdf_artifacts(once)

        self.assertEqual(once, twice)

    def test_empty_and_none_safe(self):
        self.assertEqual(clean_pdf_artifacts(""), "")
        self.assertEqual(clean_pdf_artifacts(None), "")


class StubDetectionTest(unittest.TestCase):
    def test_title_source_only_is_stub(self):
        text = (
            "제목: 고용보험법 제23조 위헌소원\n"
            "출처: https://www.law.go.kr/LSW/detcInfoP.do?mode=1&detcSeq=54676"
        )

        self.assertTrue(is_stub_content(text))

    def test_real_body_is_not_stub(self):
        text = (
            "제목: 어떤 사건\n"
            "출처: https://www.law.go.kr/x\n"
            "[요지] 실제 본문 내용이 여기 있습니다."
        )

        self.assertFalse(is_stub_content(text))

    def test_normal_content_is_not_stub(self):
        self.assertFalse(is_stub_content("남녀고용평등법 제11조는 강행규정으로서"))

    def test_empty_is_stub(self):
        self.assertTrue(is_stub_content(""))
        self.assertTrue(is_stub_content(None))


if __name__ == "__main__":
    unittest.main()
