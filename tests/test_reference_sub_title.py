import unittest

from scripts.utils.reference_sub_title import (
    extract_court_reference,
    extract_nlrc_case_number,
    legacy_pdf_case_sub_title,
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

    def test_normalizes_legacy_pdf_case_title(self):
        title = "대구고등법원-2019나23597"
        self.assertEqual(
            legacy_pdf_case_sub_title(title),
            "대구고등법원-2019나23597",
        )


if __name__ == "__main__":
    unittest.main()
