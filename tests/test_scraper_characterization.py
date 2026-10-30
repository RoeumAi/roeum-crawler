"""수정 전 동작을 고정하는 특성 테스트: 해석례·심의결정례 저장 결과, 법령 조문 분리.

네트워크·실제 DB 없이 가짜 Playwright 페이지와 가짜 컬렉션으로 돌린다.
"""
import asyncio
import unittest
from unittest.mock import patch

from scripts.decision.logic import scraper as decision_scraper
from scripts.interpretation.logic import scraper as interpretation_scraper
from scripts.law.logic.scraper import split_articles_by_number
from tests.fakes import FakeDB


class FakeEl:
    def __init__(self, tag, text="", el_id=None):
        self.tag, self.text, self.el_id = tag, text, el_id

    async def evaluate(self, js):
        return self.tag

    async def get_attribute(self, name):
        return self.el_id

    async def inner_text(self, timeout=None):
        return self.text


class FakeLocator:
    def __init__(self, items):
        self.items = items

    async def count(self):
        return len(self.items)

    async def inner_text(self, timeout=None):
        return self.items[0].text

    async def all(self):
        return self.items


class FakePage:
    def __init__(self, title, subtitle, body):
        self.selectors = {
            "#contentBody h2": [FakeEl("h2", title)] if title else [],
            "#contentBody div.subtit1": [FakeEl("div", subtitle)] if subtitle else [],
            "#contentBody h4, #contentBody .pty4": body,
        }

    def locator(self, selector):
        return FakeLocator(self.selectors.get(selector, []))

    async def goto(self, *args, **kwargs):
        return None

    async def screenshot(self, **kwargs):
        return None

    async def content(self):
        return "<html></html>"


def fake_playwright(page):
    class Browser:
        async def new_page(self):
            return page

        async def close(self):
            return None

    class Chromium:
        async def launch(self, **kwargs):
            return Browser()

    class P:
        chromium = Chromium()

    class Ctx:
        async def __aenter__(self):
            return P()

        async def __aexit__(self, *exc):
            return False

    return lambda: Ctx()


URL_ITEM = {"url": "https://www.law.go.kr/LSW/cgmExpcInfoP.do?mode=2&cgmExpcDatSeq=555&ofiClsCd=350101",
            "doc_seq": "555"}
SECTIONS = [FakeEl("h4", "【질의요지】"), FakeEl("p", "질문입니다"),
            FakeEl("h4", "【회신】"), FakeEl("p", "답변입니다")]


def run_interpretation(page, db, **kwargs):
    with patch.object(interpretation_scraper, "async_playwright", fake_playwright(page)), \
         patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
        return asyncio.run(interpretation_scraper.scrape_and_save(
            url=URL_ITEM, output_dir="/nonexistent", output_name="t", save_to_db=True, **kwargs))


class InterpretationScrapeAndSaveTest(unittest.TestCase):
    def test_success_saves_one_row_per_section(self):
        db = FakeDB()
        page = FakePage("임금 문의", "[고용노동부 임금근로시간정책팀-1, 2026. 9. 1.]", SECTIONS)
        result = run_interpretation(page, db)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["sections_count"], 2)
        self.assertEqual(len(db["interpretation"].docs), 2)
        self.assertEqual(db["interpretation"].docs[0]["metadata"]["effective"], "2026-09-01")

    def test_taxlaw_branch_saves_rows(self):
        db = FakeDB()
        body = "화면내 검색\n국세 해석\n제2026-1호\n생산일자：2026.9.1.\n회신\n답변 내용입니다"
        page = FakePage(None, "", [])
        page.selectors["body"] = [FakeEl("body", body)]
        result = run_interpretation(page, db)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(db["interpretation"].docs), 1)

    def test_save_failure_is_reported_as_failed(self):
        db = FakeDB()
        db["interpretation"].fail_update = lambda update, filter_: True
        page = FakePage("임금 문의", "[고용노동부 임금근로시간정책팀-1, 2026. 9. 1.]", SECTIONS)
        result = run_interpretation(page, db)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "MongoDB 저장 실패")
        self.assertEqual(db["interpretation"].docs, [])

    def test_zero_sections_is_reported_as_failed(self):
        db = FakeDB()
        page = FakePage("임금 문의", "[고용노동부 임금근로시간정책팀-1, 2026. 9. 1.]", [])
        result = run_interpretation(page, db)
        self.assertEqual(result["status"], "failed")
        self.assertIn("본문 섹션 없음", result["error"])
        self.assertEqual(db["interpretation"].docs, [])

    def test_partial_save_is_failed_and_marked_for_retry(self):
        db = FakeDB()
        db["interpretation"].fail_update = lambda update, filter_: filter_.get("article_number") == "2"
        page = FakePage("임금 문의", "[고용노동부 임금근로시간정책팀-1, 2026. 9. 1.]", SECTIONS)
        result = run_interpretation(page, db)
        self.assertEqual(result["status"], "failed")
        self.assertEqual([d["metadata"].get("save_incomplete") for d in db["interpretation"].docs], [True])

    def test_taxlaw_branch_with_jsonl_does_not_raise(self):
        # SAVE_JSONL=true 에서 국세청 본문 분기가 NameError 로 저장 뒤 실패 처리되던 문제
        import tempfile
        db = FakeDB()
        body = "화면내 검색\n국세 해석\n제2026-1호\n생산일자：2026.9.1.\n회신\n답변 내용입니다"
        page = FakePage(None, "", [])
        page.selectors["body"] = [FakeEl("body", body)]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(interpretation_scraper, "async_playwright", fake_playwright(page)), \
                 patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
                result = asyncio.run(interpretation_scraper.scrape_and_save(
                    url=URL_ITEM, output_dir=tmp, output_name="t", save_to_db=True, save_jsonl=True))
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(db["interpretation"].docs), 1)


class DecisionScrapeAndSaveTest(unittest.TestCase):
    ITEM = {"jgmt_sn": "77", "title": "심의 요지입니다", "registered_at": "2026-10-01", "url": "https://x/d?jgmtSn=77"}

    def run_scrape(self, db):
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
            return asyncio.run(decision_scraper.scrape_and_save(
                self.ITEM, output_dir="/nonexistent", output_name="t", save_jsonl=False))

    def test_save_success(self):
        db = FakeDB()
        result = self.run_scrape(db)
        self.assertEqual(result, {"doc_id": "77", "status": "success"})
        rows = db["decision"].docs
        self.assertEqual([r["doc_id"] for r in rows], ["77"])
        self.assertEqual(rows[0]["metadata"]["current_version"], 1)

    def test_save_failure_is_failed(self):
        db = FakeDB()
        db["decision"].insert_one = lambda doc: (_ for _ in ()).throw(RuntimeError("down"))
        self.assertEqual(self.run_scrape(db)["status"], "failed")


class SplitArticlesByNumberTest(unittest.TestCase):
    def test_splits_numbered_and_sub_numbered_articles(self):
        text = "[제1조(목적)]\n목적 내용\n[제5조의2(정의)]\n정의 내용\n[제6조(적용)]\n적용 내용"
        articles = split_articles_by_number(text)
        self.assertEqual([a["article_number"] for a in articles], ["1", "5.2", "6"])
        self.assertEqual(articles[1]["article_title"], "제5조의2(정의)")
        self.assertEqual(articles[1]["article_number_numeric"], 5.2)
        self.assertEqual(articles[2]["content"], "적용 내용")

    def test_text_without_article_markers_returns_empty(self):
        self.assertEqual(split_articles_by_number("조문 표기가 없는 본문"), [])


if __name__ == "__main__":
    unittest.main()
