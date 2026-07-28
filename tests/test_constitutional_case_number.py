import unittest

from bs4 import BeautifulSoup

from scripts.constitutional_decc.logic.scraper import _extract_case_number


class ConstitutionalCaseNumberTest(unittest.TestCase):
    def test_uses_dedicated_hidden_case_number(self):
        soup = BeautifulSoup(
            """
            <input id="detcNo" value="2019헌바454" />
            <div class="subtit1">
              [전원재판부 2019헌바454, 2022. 10. 27.]
            </div>
            """,
            "html.parser",
        )

        self.assertEqual(_extract_case_number(soup), "2019헌바454")

    def test_falls_back_to_case_number_inside_subtitle(self):
        soup = BeautifulSoup(
            """
            <div class="subtit1">
              [전원재판부 93헌마45, 1993. 9. 27., 각하]
            </div>
            """,
            "html.parser",
        )

        self.assertEqual(_extract_case_number(soup), "93헌마45")

    def test_returns_empty_when_case_number_is_absent(self):
        soup = BeautifulSoup("<div class='subtit1'>사건번호 없음</div>", "html.parser")

        self.assertEqual(_extract_case_number(soup), "")


if __name__ == "__main__":
    unittest.main()
