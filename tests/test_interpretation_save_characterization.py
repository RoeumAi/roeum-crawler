"""해석례 상세 수집(scrape_and_save)의 저장 행·결과·입력 형태별 동작을 고정하는 특성 테스트."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.interpretation.logic import scraper as interpretation_scraper
from tests.fakes import FakeDB
from tests.test_scraper_characterization import SECTIONS, FakeEl, FakePage, fake_playwright

SUBTITLE = "[고용노동부 임금근로시간정책팀-1, 2026. 9. 1.]"
DETAIL = "https://www.law.go.kr/LSW/cgmExpcInfoP.do?mode=2&cgmExpcDatSeq=555&ofiClsCd=350101"


def make_page(subtitle=SUBTITLE, sections=SECTIONS):
    page = FakePage("임금  문의", subtitle, sections)
    page.visited = []

    async def goto(url, *args, **kwargs):
        page.visited.append(url)

    page.goto = goto
    return page


def run(page, url, db=None, output_dir="/nonexistent", **kwargs):
    db = FakeDB() if db is None else db
    kwargs.setdefault("save_to_db", True)
    with patch.object(interpretation_scraper, "async_playwright", fake_playwright(page)), \
         patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
        result = asyncio.run(interpretation_scraper.scrape_and_save(
            url=url, output_dir=output_dir, output_name="t", **kwargs))
    return result, db


class InterpretationRowsTest(unittest.TestCase):
    def test_row_schema(self):
        page = make_page()
        result, db = run(page, {"url": DETAIL, "doc_seq": "555", "ofi_cls_cd": "350101"})
        self.assertEqual(result, {"status": "success", "doc_id": "555", "title": "임금  문의", "sections_count": 2})
        self.assertEqual(page.visited, [DETAIL])
        first, second = db["interpretation"].docs
        self.assertEqual(
            {k: v for k, v in first.items() if k not in ("_id", "metadata", "content_hash", "source_version_id")},
            {"doc_id": "555", "article_number": "1", "chunk_id": "interpretation:555:article:1",
             "article_title": "질의요지", "title": "임금 문의", "content": "질문입니다", "doc_type": "법령해석",
             "sub_title": "고용노동부, 임금근로시간정책팀-1, 2026.9.1"})
        meta = first["metadata"]
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(
            {k: v for k, v in meta.items() if k != "updated_at"},
            {"source_url": DETAIL, "source_type": "web", "effective": "2026-09-01", "is_active": True,
             "cpt_ofi_code": "1492000", "cpt_ofi_name": "고용노동부", "ofi_cls_cd": "350101",
             "article_index": 1, "total_articles": 2, "created_at": today})
        self.assertEqual((second["article_number"], second["metadata"]["article_index"]), ("2", 2))

    def test_resave_updates_in_place_and_keeps_created_at(self):
        page = make_page()
        _, db = run(page, {"url": DETAIL, "doc_seq": "555"})
        db["interpretation"].docs[0]["metadata"]["created_at"] = "2020-01-01"
        run(page, {"url": DETAIL, "doc_seq": "555"}, db=db)
        self.assertEqual(len(db["interpretation"].docs), 2)
        self.assertEqual(db["interpretation"].docs[0]["metadata"]["created_at"], "2020-01-01")

    def test_doc_seq_is_taken_from_url_when_item_lacks_it(self):
        for url in ({"url": DETAIL}, DETAIL):
            result, db = run(make_page(), url)
            self.assertEqual(result["doc_id"], "555")
            self.assertEqual(db["interpretation"].docs[0]["metadata"]["ofi_cls_cd"], "350101")

    def test_cgm_expc_seq_parameter_is_accepted(self):
        url = "https://www.law.go.kr/LSW/cgmExpcInfoP.do?cgmExpcSeq=777"
        result, _ = run(make_page(), url)
        self.assertEqual(result["doc_id"], "777")

    def test_ofi_cls_cd_from_item_is_stored(self):
        _, db = run(make_page(), {"url": DETAIL, "doc_seq": "555", "ofi_cls_cd": "350102"})
        self.assertEqual(db["interpretation"].docs[0]["metadata"]["ofi_cls_cd"], "350102")

    def test_missing_doc_seq_fails_before_save(self):
        result, db = run(make_page(), "https://www.law.go.kr/LSW/other.do?x=1")
        self.assertEqual(result, {"status": "failed", "error": "No doc_seq"})
        self.assertEqual(dict(db), {})

    def test_unparseable_date_falls_back_to_today(self):
        _, db = run(make_page(subtitle="[고용노동부 임금근로시간정책팀-1]"), {"url": DETAIL, "doc_seq": "555"})
        meta = db["interpretation"].docs[0]["metadata"]
        self.assertEqual(meta["effective"], datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(db["interpretation"].docs[0]["sub_title"], "고용노동부 임금근로시간정책팀-1")

    def test_save_to_db_false_writes_nothing(self):
        result, db = run(make_page(), {"url": DETAIL, "doc_seq": "555"}, save_to_db=False)
        self.assertEqual(result["status"], "success")
        self.assertEqual(dict(db), {})

    def test_sections_keep_header_order_and_merge_repeated_headers(self):
        sections = [FakeEl("h4", "【질의요지】"), FakeEl("p", "가"), FakeEl("h4", "【회신】"), FakeEl("p", "나"),
                    FakeEl("h4", "【질의요지】"), FakeEl("p", "다")]
        _, db = run(make_page(sections=sections), {"url": DETAIL, "doc_seq": "555"})
        self.assertEqual([(d["article_title"], d["content"]) for d in db["interpretation"].docs],
                         [("질의요지", "가\n다"), ("회신", "나")])

    def test_notice_header_is_skipped(self):
        sections = [FakeEl("h4", "【안내】", el_id="expcNotice"), FakeEl("h4", "【회신】"), FakeEl("p", "나")]
        _, db = run(make_page(sections=sections), {"url": DETAIL, "doc_seq": "555"})
        self.assertEqual([d["article_title"] for d in db["interpretation"].docs], ["회신"])

    def test_jsonl_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run(make_page(), {"url": DETAIL, "doc_seq": "555"}, output_dir=tmp, save_jsonl=True)
            self.assertEqual(result["status"], "success")
            with open(os.path.join(tmp, "t_임금  문의.jsonl"), encoding="utf-8") as f:
                data = json.loads(f.read())
        self.assertEqual(data, {
            "doc_id": "555", "doc_title": "임금 문의", "sub_title": "고용노동부, 임금근로시간정책팀-1, 2026.9.1",
            "department": "고용노동부", "doc_number": "임금근로시간정책팀-1", "doc_date": "2026.9.1",
            "source_url": DETAIL, "sections": {"질의요지": "질문입니다\n", "회신": "답변입니다\n"}})

    def test_source_url_is_built_when_item_has_no_detail_url_but_string_url_does(self):
        # 문자열 URL 은 그대로 원본 주소가 된다
        _, db = run(make_page(), DETAIL)
        self.assertEqual(db["interpretation"].docs[0]["metadata"]["source_url"], DETAIL)

    def test_page_error_returns_failed_and_dumps_debug_files(self):
        page = make_page()

        async def broken(*args, **kwargs):
            raise RuntimeError("nav down")

        page.goto = broken
        with tempfile.TemporaryDirectory() as tmp, patch.object(interpretation_scraper, "project_root", tmp):
            result, db = run(page, {"url": DETAIL, "doc_seq": "555"})
            dumped = os.listdir(os.path.join(tmp, "debug"))
        self.assertEqual(result, {"status": "failed", "error": "nav down", "doc_id": None})
        self.assertEqual(dict(db), {})
        self.assertEqual(sorted(os.path.splitext(n)[1] for n in dumped), [".html"])


if __name__ == "__main__":
    unittest.main()
