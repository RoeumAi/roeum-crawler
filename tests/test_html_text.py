"""원문 `<p>` 문단을 살린다(예전에는 공백으로 이어 붙여 한 줄이 됐다)."""
import unittest

from bs4 import BeautifulSoup

from scripts.admin_decc.logic.scraper import parse_content
from scripts.utils.html_text import block_text

DECC = """
<h4>【이유】</h4>
<p class="pty6">1. 사건개요</p>
<p class="pty6_dep1_1">청구인이 장려금을 신청하자,<br/>피청구인은 거부하였다.</p>
<br/>
<p class="pty6">2. 청구인 주장</p>
<p class="pty6_dep1_1">위법ㆍ부당하다.</p>
"""


class BlockTextTest(unittest.TestCase):
    def test_paragraphs_and_line_breaks(self):
        div = BeautifulSoup("<div><p>가.  첫 문단</p><p>둘째<br>줄</p></div>", "html.parser").div
        self.assertEqual(block_text(div), "가. 첫 문단\n\n둘째\n줄")

    def test_decision_reason_keeps_paragraphs(self):
        chunks = parse_content(DECC, "1", "u", "t")
        reason = [c for c in chunks if c["title"] == "이유"][0]["text"]
        self.assertEqual(reason.split("\n\n"), [
            "1. 사건개요", "청구인이 장려금을 신청하자,\n피청구인은 거부하였다.", "2. 청구인 주장", "위법ㆍ부당하다.",
        ])


if __name__ == "__main__":
    unittest.main()
