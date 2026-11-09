"""본문이 첨부에 있는 행정규칙: 판정·원문 첨부 고르기·적용 기간 읽기·수집 경로·다시 넣기 도구."""
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts.adrule.logic import attachment_body as ab
from scripts.adrule.logic import scraper as adrule
from scripts.adrule.logic.scraper import SaveTarget
from scripts.core.database.unified_repository import UnifiedDocumentRepository
from scripts.migrations import adrule_attachment_body as tool
from tests.fakes import FakeDB


def record(body, structured="N", links=(), names=()):
    files = {"첨부파일링크": list(links), "첨부파일명": list(names)} if links else None
    return {"AdmRulService": {"행정규칙기본정보": {"조문형식여부": structured, "행정규칙ID": "2002374"},
                              "조문내용": body, **({"첨부파일": files} if files else {})}}


# law.go.kr 상세 API(2026-11-09) 응답 요약
MINWAGE_2015 = record("1. 최저임금액<img id=\"17687027\"></img>　　2. 업종 구분 여부 … 3. 최저임금 적용 기간 : 2015. 1. 1. ~ 2015. 12. 31.",
                      links=["http://law.go.kr/flDownload.do?flSeq=17949655"], names=["(2015년도) 최저임금 고시.hwp"])
MINWAGE_2022 = record("「2022년 적용 최저임금 고시」의 자세한 내용은 상단 메뉴 \"<img id=\"40425753\"></img>\"버튼을 이용하십시오.",
                      structured="Y",
                      links=["http://law.go.kr/flDownload.do?flSeq=106220845", "http://law.go.kr/flDownload.do?flSeq=106220847"],
                      names=["2022년 적용 최저임금 고시.pdf", "최저임금 고시 재개정 이유서.hwp"])


class NeedsAttachmentBodyTest(unittest.TestCase):
    def test_pointer_to_attachment_needs_it_even_in_article_form(self):
        self.assertTrue(ab.needs_attachment_body(MINWAGE_2022))

    def test_table_image_in_unstructured_body_needs_it(self):
        self.assertTrue(ab.needs_attachment_body(MINWAGE_2015))

    def test_image_inside_article_form_keeps_screen_parser(self):
        self.assertFalse(ab.needs_attachment_body(record("제1조(목적) <img id=\"1\"></img>", structured="Y")))

    def test_plain_text_or_missing_record_keeps_screen_parser(self):
        self.assertFalse(ab.needs_attachment_body(record("1. 상한액은 다음과 같다.")))
        self.assertFalse(ab.needs_attachment_body(None))


class NoticeFilesTest(unittest.TestCase):
    def test_supplements_are_left_out(self):
        self.assertEqual([("https://law.go.kr/flDownload.do?flSeq=106220845", "2022년 적용 최저임금 고시.pdf")],
                         ab.notice_files(MINWAGE_2022))

    def test_same_file_in_pdf_and_hwp_reads_pdf_and_drafts_are_dropped(self):
        rec = record("<img>", links=["l1", "l2", "l3", "l4"],
                     names=["고용위기지역 지정 고시 일부개정(안).hwpx", "고용위기지역 지정 고시.hwp",
                            "고용위기지역 지정 고시.pdf", "제개정이유서(고용위기지역 지정 고시).hwpx"])
        self.assertEqual([("l3", "고용위기지역 지정 고시.pdf")], ab.notice_files(rec))

    def test_draft_named_file_is_the_notice_when_nothing_else_is(self):
        rec = record("<img>", links=["a", "b"], names=["최저임금_결정_고시안.hwp", "개정이유서.hwp"])
        self.assertEqual([("a", "최저임금_결정_고시안.hwp")], ab.notice_files(rec))

    def test_unreadable_formats_are_skipped(self):
        rec = record("<img>", links=["a", "b"], names=["기록관리기준표.xls", "기록관리기준표 고시문.hwp"])
        self.assertEqual([("b", "기록관리기준표 고시문.hwp")], ab.notice_files(rec))


class StatedPeriodTest(unittest.TestCase):
    def test_reads_period_in_several_spellings(self):
        self.assertEqual(("2027-01-01", "2027-12-31"), ab.stated_period("## 3. 최저임금 적용 기간: 2027. 1. 1. ~ 2027. 12. 31."))
        self.assertEqual(("2008-01-01", "2008-12-31"), ab.stated_period("3. 적용 기간 : 2008.1.1.～2008.12.31."))

    def test_missing_or_impossible_period_is_none(self):
        self.assertIsNone(ab.stated_period("적용 기간은 별도로 정한다"))
        self.assertIsNone(ab.stated_period("적용 기간 : 2027. 2. 30. ~ 2027. 12. 31."))
        self.assertIsNone(ab.stated_period("적용 기간 : 2027. 12. 31. ~ 2027. 1. 1."))


def page_with(html):
    page = Mock()
    page.content = AsyncMock(return_value=html)
    locator = Mock()
    locator.inner_html = AsyncMock(return_value="<div class='lawcon'><p>1. 최저임금액</p></div>")
    page.locator = Mock(return_value=locator)
    return page


class ParseRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_pointer_page_reads_the_notice_attachment(self):
        extracted = {"structured_text": "2022년 적용 최저임금 고시\n\n| 시간급 | 9,160원 |", "extraction_status": "complete"}
        with patch.object(adrule, "_download_attachment", return_value=extracted) as download:
            chunks = await adrule.parse_law_html(page_with("<div>본문</div>"), "2100000203520", "2022 고시", "u", "o",
                                                 lawgokr_record=MINWAGE_2022)
        download.assert_called_once_with("https://law.go.kr/flDownload.do?flSeq=106220845", "2022년 적용 최저임금 고시.pdf")
        self.assertIn("9,160원", chunks[0]["text"])
        self.assertEqual("106220845", chunks[0]["metadata"]["source_file_key"])

    async def test_pointer_without_notice_attachment_fails_instead_of_saving_the_pointer(self):
        with self.assertRaises(ValueError):
            await adrule.parse_law_html(page_with("<div>본문</div>"), "d", "t", "u", "o",
                                        lawgokr_record=record("자세한 내용은 상단 메뉴 \"<img>\"버튼을 이용하십시오.",
                                                              links=["a"], names=["개정이유서.hwp"]))

    async def test_image_without_notice_attachment_keeps_screen_text(self):
        # 장식 그림뿐이고 원문 첨부가 없으면 날마다 실패하지 않도록 화면 글을 저장한다.
        with patch.object(adrule, "_download_attachment") as download:
            chunks = await adrule.parse_law_html(page_with("<div>본문</div>"), "d", "t", "u", "o",
                                                 lawgokr_record=record("1. 최저임금액 <img id=\"1\"></img>"))
        download.assert_not_called()
        self.assertIn("최저임금액", chunks[0]["text"])

    async def test_unknown_record_fails_unstructured_page(self):
        with self.assertRaises(ValueError):
            await adrule.parse_law_html(page_with("<div>본문</div>"), "d", "t", "u", "o", lawgokr_record=None)

    async def test_plain_page_still_uses_screen_parser(self):
        with patch.object(adrule, "_download_attachment") as download:
            chunks = await adrule.parse_law_html(page_with("<div>본문</div>"), "d", "t", "u", "o",
                                                 lawgokr_record=record("1. 최저임금액 10,320원"))
        download.assert_not_called()
        self.assertIn("최저임금액", chunks[0]["text"])


class SaveRowsTest(unittest.TestCase):
    def save(self, text, target=SaveTarget(), db=None):
        db = db if db is not None else FakeDB()
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db), \
             patch.object(adrule, "collect_annex_articles", return_value=([], 0)), \
             patch("scripts.core.database.unified_repository.UnifiedDocumentRepository._ensure_indexes",
                   create=True, return_value=None):
            action = adrule.save_to_mongodb([{"title": "", "text": text}], "2027년 적용 최저임금 고시", "2100000283564",
                                            "u", effective_date="2027-01-01", lawgokr_record={}, target=target)
        return action, db

    def active(self, db, name="adrule"):
        return [d for d in db[name].docs if d.get("metadata", {}).get("is_active") is not False]

    def test_stated_period_end_is_stored_on_current_rows(self):
        _, db = self.save("고시\n\n3. 최저임금 적용 기간: 2027. 1. 1. ~ 2027. 12. 31.")
        self.assertEqual({"2027-12-31"}, {d["metadata"].get("effective_to") for d in self.active(db)})

    def test_history_meta_overrides_stated_period(self):
        _, db = self.save("적용 기간: 2027. 1. 1. ~ 2027. 12. 31.",
                          SaveTarget("adrule_history", {"effective_to": "2027-06-30"}))
        self.assertEqual({"2027-06-30"}, {d["metadata"]["effective_to"] for d in self.active(db, "adrule_history")})

    def test_replace_rows_retires_rows_not_written_this_time(self):
        db = FakeDB()
        db["adrule"].docs.append({"_id": 99, "doc_id": "2100000283564", "chunk_id": "old:article_7",
                                  "metadata": {"is_active": True}})
        action, db = self.save("1. 최저임금액 10,700원", SaveTarget(replace_rows=True), db)
        self.assertTrue(action)
        self.assertNotIn("old:article_7", [d["chunk_id"] for d in self.active(db)])
        self.assertTrue(self.active(db))

    def test_daily_save_leaves_other_rows_alone(self):
        db = FakeDB()
        db["adrule"].docs.append({"_id": 99, "doc_id": "2100000283564", "chunk_id": "old:article_7",
                                  "metadata": {"is_active": True}})
        _, db = self.save("1. 최저임금액 10,700원", db=db)
        self.assertIn("old:article_7", [d["chunk_id"] for d in self.active(db)])


class RetireRowsOutsideTest(unittest.TestCase):
    def test_keeps_written_rows_and_their_search_parts(self):
        db = FakeDB()
        rows = db["adrule"].docs
        rows += [{"doc_id": "d", "chunk_id": "keep", "metadata": {"is_active": True}},
                 {"doc_id": "d", "chunk_id": "keep:part1", "metadata": {"is_active": True, "parent_chunk_id": "keep"}},
                 {"doc_id": "d", "chunk_id": "stale", "metadata": {"is_active": True}},
                 {"doc_id": "other", "chunk_id": "x", "metadata": {"is_active": True}}]
        repo = UnifiedDocumentRepository.__new__(UnifiedDocumentRepository)
        repo.collection = db["adrule"]
        self.assertEqual(1, repo.retire_rows_outside("d", ["keep"]))
        self.assertEqual(["keep", "keep:part1", "x"],
                         [r["chunk_id"] for r in rows if r["metadata"]["is_active"]])


class AttachmentBodyToolTest(unittest.TestCase):
    def test_partial_history_version_is_turned_off_after_rescrape(self):
        db = self.db()

        async def scrape(url, *_args, target=None, **_kw):
            db["adrule_history"].docs.append({"doc_id": "2200000010021", "chunk_id": "new",
                                              "metadata": {"is_active": True, "pdf_extraction_status": "partial"}})
            return {"status": "success"}

        target = tool.plan(db, self.args(), self.fetch)[0]
        with patch("scripts.adrule.logic.scraper.scrape_and_save", side_effect=scrape):
            import asyncio
            results = asyncio.run(tool._rescrape(db, [target], out=lambda *_: None))
        self.assertEqual("incomplete", results[0]["status"])
        self.assertFalse([d for d in db["adrule_history"].docs
                          if d["doc_id"] == "2200000010021" and d["metadata"].get("is_active")])

    def db(self):
        db = FakeDB()
        db["adrule_history"].docs += [
            {"doc_id": "2200000010021", "title": "(2015년도) 최저임금 고시",
             "metadata": {"is_active": True, "adrule_id": "2002374", "source_url": "u2015", "history": True,
                          "is_upcoming": False, "effective_to": "2015-12-31", "superseded_by": "2200000038564",
                          "dept_code": "1492000"}},
            {"doc_id": "2100000227560", "title": "2024년 적용 최저임금 고시",
             "metadata": {"is_active": True, "adrule_id": "2002374", "source_file_keys": ["1"]}},
        ]
        db["adrule"].docs += [{"doc_id": "plain", "title": "평문 고시", "metadata": {"is_active": True}}]
        return db

    def fetch(self, target, doc_id):
        return MINWAGE_2015 if doc_id == "2200000010021" else record("1. 평문")

    def args(self, apply=False):
        return types.SimpleNamespace(apply=apply, adrule_id=None, limit=0)

    def test_plan_picks_screen_saved_docs_whose_body_is_in_attachments(self):
        targets = tool.plan(self.db(), self.args(), self.fetch)
        self.assertEqual([("adrule_history", "2200000010021")], [(t["collection"], t["doc_id"]) for t in targets])
        self.assertEqual(["(2015년도) 최저임금 고시.hwp"], [name for _, name in targets[0]["files"]])

    def test_history_version_turned_off_by_partial_repair_is_planned_again(self):
        db = self.db()
        for row in db["adrule_history"].docs:
            if row["doc_id"] == "2200000010021":
                row["metadata"]["is_active"] = False
        db["adrule_history"].docs.append({"doc_id": "2200000010021", "chunk_id": "new",
                                          "metadata": {"is_active": False, "source_file_keys": ["9"],
                                                       "pdf_extraction_status": "partial"}})
        targets = tool.plan(db, self.args(), self.fetch)
        self.assertEqual(["2200000010021"], [t["doc_id"] for t in targets])
        self.assertEqual("u2015", targets[0]["url"])

    def test_failed_rescrape_keeps_old_history_rows(self):
        db = self.db()

        async def scrape(*_args, **_kw):
            return {"status": "failed", "error": "추출 서버 멈춤"}

        target = tool.plan(db, self.args(), self.fetch)[0]
        with patch("scripts.adrule.logic.scraper.scrape_and_save", side_effect=scrape):
            import asyncio
            results = asyncio.run(tool._rescrape(db, [target], out=lambda *_: None))
        self.assertEqual("failed", results[0]["status"])
        self.assertTrue(db["adrule_history"].docs[0]["metadata"]["is_active"])

    def test_history_target_keeps_period_and_replaces_rows(self):
        target = tool.plan(self.db(), self.args(), self.fetch)[0]
        save = tool.save_target(target)
        self.assertEqual("adrule_history", save.collection)
        self.assertTrue(save.replace_rows)
        self.assertEqual({"history": True, "is_upcoming": False, "effective_to": "2015-12-31",
                          "superseded_by": "2200000038564"}, dict(save.extra_meta))

    def test_preview_does_not_rescrape(self):
        rescrape = AsyncMock()
        self.assertEqual(0, tool.run(self.args(), self.db(), self.fetch, rescrape, out=lambda *_: None))
        rescrape.assert_not_called()

    def test_apply_reports_failures(self):
        async def rescrape(db, targets, out):
            return [{**t, "status": "failed", "error": "추출 실패"} for t in targets]

        self.assertEqual(1, tool.run(self.args(apply=True), self.db(), self.fetch, rescrape, out=lambda *_: None))


if __name__ == "__main__":
    unittest.main()
