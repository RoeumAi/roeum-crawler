import unittest

from scripts.utils.reference_sub_title import (
    extract_court_reference,
    extract_nlrc_case_number,
    extract_nlrc_decision_date,
    legacy_pdf_case_sub_title,
    nlrc_judgment_sub_title,
)


class ReferenceSubTitleTest(unittest.TestCase):
    def test_extracts_court_reference_from_decision_title(self):
        title = "징계 제외가 구제대상인지 여부(대법원 2016두32961)"
        self.assertEqual(extract_court_reference(title), "대법원 2016두32961")

    def test_leaves_decision_without_case_number_empty(self):
        self.assertEqual(extract_court_reference("2019년 월간 소송 동향"), "")

    def test_extracts_nlrc_case_number_and_range(self):
        self.assertEqual(
            extract_nlrc_case_number("조정 성립 사례[경남2018조정40~46]"),
            "경남2018조정40~46",
        )

    def test_extracts_nlrc_case_number_kinds_found_in_titles(self):
        for title, expected in (
            ("별도의 교섭단위로 분리할 필요성이 있음[중앙2018단위3]", "중앙2018단위3"),
            ("근로자성을 인정한 사례[중앙2018재단위1]", "중앙2018재단위1"),
            ("휴업수당 감액대상이 아니라고 결정한 사례[중앙2018휴업1]", "중앙2018휴업1"),
            ("재처분 사례[중앙2018재부해2]", "중앙2018재부해2"),
        ):
            with self.subTest(title=title):
                self.assertEqual(extract_nlrc_case_number(title), expected)

    def test_decision_date_forms(self):
        for body, expected in (
            ("사례 3 [2026. 3. 23. 판정 / 초심: 기각, 재심: 초심취소] 사용자가", "2026-03-23"),
            ("사례 4 [2024. 7. 29.. 판정 / 초심: 인정]", "2024-07-29"),
            ("부당해고 구제 재심신청 ■ 판정일자 : 2016. 11. 17. ■ 판정결과", "2016-11-17"),
            ("대리인 공인노무사 ○○○ 판 정 일 2018. 5. 25. 우리 위원회는", "2018-05-25"),
            ("■ 판정일: 2011. 12. 27. ■", "2011-12-27"),
            ("결 정 일 2016. 7. 1. 우리 위원회는", "2016-07-01"),
            ("조○○ 일 2018. 5. 23. 판 정 우리 위원회는 위 재심신청사건에 대하여", "2018-05-23"),
        ):
            with self.subTest(body=body):
                self.assertEqual(extract_nlrc_decision_date(body), expected)

    def test_decision_date_absent(self):
        self.assertEqual(extract_nlrc_decision_date(""), "")
        self.assertEqual(extract_nlrc_decision_date("2023. 12. 20. 입사하여 주문과 같이 판정한다."), "")

    def test_decision_date_prefers_own_over_first_instance(self):
        body = "초심 판정일 2011. 3. 2. 재심신청 ■ 판정일자 : 2011. 12. 27. ■"
        self.assertEqual(extract_nlrc_decision_date(body), "2011-12-27")

    def test_decision_date_skips_implausible_candidates(self):
        body = "■ 판정일자 : 2019. 13. 40. ■ 판정일 2030. 1. 1. 판정일 2010. 1. 1. 판 정 일 2018. 5. 25."
        self.assertEqual(
            extract_nlrc_decision_date(body, case_number="중앙2018부해1", registered_at="2018-08-15"),
            "2018-05-25",
        )

    def test_judgment_sub_title_forms(self):
        self.assertEqual(
            nlrc_judgment_sub_title("중앙2025부해1459", "2026-01-07"),
            "중앙노동위원회 2026. 1. 7. 판정 2025부해1459",
        )
        self.assertEqual(
            nlrc_judgment_sub_title("경기2025부해2683", "2025-10-15"),
            "경기지방노동위원회 2025. 10. 15. 판정 2025부해2683",
        )
        self.assertEqual(nlrc_judgment_sub_title("", "2026-03-23"), "2026. 3. 23. 판정")
        self.assertEqual(nlrc_judgment_sub_title("중앙2025부해1459", ""), "중앙2025부해1459")
        self.assertEqual(nlrc_judgment_sub_title("", ""), "")

    def test_normalizes_legacy_pdf_case_title(self):
        title = "대구고등법원-2019나23597"
        self.assertEqual(
            legacy_pdf_case_sub_title(title),
            "대구고등법원-2019나23597",
        )

    def test_supports_legacy_case_code_variants(self):
        for title in (
            "대법원 2004. 1. 15.자 2002무30 결정",
            "인천지방법원-2010고정2472",
            "서울행정법원-99구2832",
            "헌법재판소 2022. 8. 31.자 2020헌마1025 결정",
        ):
            with self.subTest(title=title):
                self.assertEqual(legacy_pdf_case_sub_title(title), title)


if __name__ == "__main__":
    unittest.main()
