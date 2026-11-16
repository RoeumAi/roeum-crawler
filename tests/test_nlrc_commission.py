import unittest

from scripts.utils.nlrc_commission import commission


class CommissionTest(unittest.TestCase):
    def test_subtitle_forms(self):
        cases = {
            "노동위원회(중앙) 2008. 9. 4. 판정 2008부해486": "중앙노동위원회",
            "중앙노동위원회 2018. 8. 29. 판정 중앙2018부해695": "중앙노동위원회",
            "지방노동위원회(서울) 2026. 7. 14. 판정 2026부해1628": "서울지방노동위원회",
            "지방노동위원회 (경기) 2025. 2. 24. 판정 2024부해4350": "경기지방노동위원회",
            "경남지방노동위원회 2022. 6. 23. 판정 2022부노14": "경남지방노동위원회",
            "사건번호 : 국행심 00-2510, 선고일자 : 2000-08-30": "국무총리행정심판위원회",
            "지방노동위원회() 2002. 10. 29. 판정 2002부해252": "지방노동위원회",
        }
        for sub, expected in cases.items():
            with self.subTest(sub=sub):
                self.assertEqual(commission(sub), expected)

    def test_case_number_in_title_and_content_fallback(self):
        self.assertEqual(commission("", "경기2024부해4350"), "경기지방노동위원회")
        self.assertEqual(commission("2025. 8. 11. 판정", "", "부산지방노동위원회가 판정한 사건"), "부산지방노동위원회")

    def test_unknown_is_empty(self):
        self.assertEqual(commission("2025. 8. 11. 판정", "해고 사례", "판정사항: 해고"), "")


if __name__ == "__main__":
    unittest.main()
