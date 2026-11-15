import unittest

from bs4 import BeautifulSoup

from scripts.core import lawgokr_markup as mk

# 2026-11-15 개편 뒤 헌재결정례 상세 화면 골격(실제 마크업에서 본문만 줄임)
NEW_PAGE = """<div id="contentBody"><div id="conTop" class="cont_top"><a id="top"></a>
<h2>구 노동조합 및 노동관계조정법 제90조 위헌소원</h2><div class="ct_sub">[전원재판부 2022헌바79, 2026. 8. 27.]</div></div>
<div id="conScroll"><!-- 조문정보 --><div class="pgroup conScroll">
<h4>【판시사항】</h4><p class="pty4">가. 판시 내용</p>
<h4>【결정요지】</h4><p class="pty4">결정 요지</p>
</div></div></div>"""

# 개편 전 화면
OLD_PAGE = """<div id="contentBody"><div id="conTop"><h2>제목</h2><div class="subtit1">[부제]</div></div>
<div id="conScroll"><h4>【판시사항】</h4><p>내용</p></div></div>"""


class SubtitleTest(unittest.TestCase):
    def test_new_and_old_subtitle(self):
        self.assertEqual(mk.subtitle_text(BeautifulSoup(NEW_PAGE, "html.parser")),
                         "[전원재판부 2022헌바79, 2026. 8. 27.]")
        self.assertEqual(mk.subtitle_text(BeautifulSoup(OLD_PAGE, "html.parser")), "[부제]")

    def test_nested_subtitle_is_joined_like_before(self):
        soup = BeautifulSoup('<div class="ct_sub">[전원재판부 <span>2022헌바79</span>, 2026. 8. 27.]</div>', "html.parser")
        self.assertEqual(mk.subtitle_text(soup), "[전원재판부 2022헌바79, 2026. 8. 27.]")

    def test_missing_subtitle_is_empty(self):
        self.assertEqual(mk.subtitle_text(BeautifulSoup("<div></div>", "html.parser")), "")


class ContentHtmlTest(unittest.TestCase):
    def test_new_page_unwraps_inner_group_so_sections_are_top_level(self):
        inner = BeautifulSoup(mk.content_html(BeautifulSoup(NEW_PAGE, "html.parser")), "html.parser")
        tops = [el.name for el in inner.children if getattr(el, "name", None)]
        self.assertEqual(tops, ["h4", "p", "h4", "p"])

    def test_old_page_unchanged(self):
        inner = BeautifulSoup(mk.content_html(BeautifulSoup(OLD_PAGE, "html.parser")), "html.parser")
        self.assertEqual([el.name for el in inner.children if getattr(el, "name", None)], ["h4", "p"])

    def test_no_content_is_empty(self):
        self.assertEqual(mk.content_html(BeautifulSoup("<div></div>", "html.parser")), "")


if __name__ == "__main__":
    unittest.main()
