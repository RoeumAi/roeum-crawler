import unittest

from bs4 import BeautifulSoup

from scripts.constitutional_decc.logic.scraper import _extract_sub_title


class ConstitutionalSubTitleTest(unittest.TestCase):
    def test_uses_full_decision_subtitle(self):
        soup = BeautifulSoup(
            """
            <input id="detcNo" value="2019헌바454" />
            <div class="subtit1">
              [전원재판부 2019헌바454, 2022. 10. 27.]
            </div>
            """,
            "html.parser",
        )

        self.assertEqual(
            _extract_sub_title(soup),
            "전원재판부 2019헌바454, 2022. 10. 27.",
        )

    def test_removes_only_outer_brackets(self):
        soup = BeautifulSoup(
            """
            <div class="subtit1">
              [전원재판부 93헌마45, 1993. 9. 27., 각하]
            </div>
            """,
            "html.parser",
        )

        self.assertEqual(
            _extract_sub_title(soup),
            "전원재판부 93헌마45, 1993. 9. 27., 각하",
        )

    def test_returns_empty_when_subtitle_is_absent(self):
        soup = BeautifulSoup("<div>부제 없음</div>", "html.parser")

        self.assertEqual(_extract_sub_title(soup), "")


if __name__ == "__main__":
    unittest.main()
