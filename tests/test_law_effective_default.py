"""법령: 시행일자를 못 읽어도 오늘 날짜를 넣지 않는다(넣으면 매일 새 버전·매일 재수집)."""
import unittest
from datetime import datetime

from crawl import filter_new_urls
from scripts.core.database.change_detector import ChangeDetector
from scripts.law.logic.scraper import apply_list_effective, parse_law_html

BODY = ('<div id="conTop"><h2>테스트법</h2></div><div class="ct_sub">{sub}</div>'
        '<div id="contentBody"><div class="pgroup"><span class="bl"><label>제1조(목적)</label></span>'
        '<p>이 법은 목적을 정한다.</p></div></div>')
URL = "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=100"


class ParseEffectiveTest(unittest.TestCase):
    def test_unreadable_effective_is_empty_not_today(self):
        doc = parse_law_html(BODY.format(sub="고용노동부령 제1호"), URL)
        self.assertEqual(doc["metadata"]["effective"], "")
        self.assertNotEqual(doc["metadata"]["effective"], datetime.now().strftime("%Y-%m-%d"))

    def test_readable_effective_is_kept(self):
        doc = parse_law_html(BODY.format(sub="[시행 2026. 1. 5.] 고용노동부령 제1호"), URL)
        self.assertEqual(doc["metadata"]["effective"], "2026-01-05")

    def test_effective_equal_to_today_is_not_overwritten_by_fallback_tag(self):
        today = datetime.now()
        sub = f"[시행 {today.year}. {today.month}. {today.day}.] 고용노동부령 제1호"
        html = BODY.format(sub=sub).replace('<div id="contentBody">', '<div class="ct_info">공포 2020. 1. 1.</div><div id="contentBody">')
        doc = parse_law_html(html, URL)
        self.assertEqual(doc["metadata"]["effective"], today.strftime("%Y-%m-%d"))


class ListEffectiveFallbackTest(unittest.TestCase):
    def doc(self, effective=""):
        return {"metadata": {"effective": effective}}

    def test_fills_empty_effective_from_list(self):
        doc = self.doc()
        apply_list_effective(doc, "20260105")
        self.assertEqual(doc["metadata"]["effective"], "2026-01-05")

    def test_keeps_parsed_effective(self):
        doc = self.doc("2026-02-01")
        apply_list_effective(doc, "20260105")
        self.assertEqual(doc["metadata"]["effective"], "2026-02-01")

    def test_sentinel_or_garbage_stays_empty(self):
        for value in ("99991231", "", None, "abc"):
            doc = self.doc()
            apply_list_effective(doc, value)
            self.assertEqual(doc["metadata"]["effective"], "")

    def test_filled_value_does_not_trigger_recollection_next_run(self):
        doc = self.doc()
        apply_list_effective(doc, "20260105")
        item = {"url": URL, "effective": "20260105"}
        self.assertEqual(filter_new_urls([item], set(), effective_map={URL: doc["metadata"]["effective"]}), [])

    def test_empty_effective_is_not_a_date_change_for_stored_versions(self):
        # 저장소는 새 문서의 시행일자가 비면 날짜 변경으로 보지 않는다(내용이 같으면 새 버전 없음).
        old = {"title": "t", "sub_title": "s", "content": "c", "metadata": {"effective": "2026-01-05"}}
        new = {"title": "t", "sub_title": "s", "content": "c", "metadata": {"effective": ""}}
        self.assertFalse(ChangeDetector.needs_new_version(old, new, effective_date_changed=False))


if __name__ == "__main__":
    unittest.main()
