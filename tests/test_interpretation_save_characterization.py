"""해석례 상세 수집(scrape_and_save)의 저장 행·결과·입력 형태별 동작을 고정하는 특성 테스트.

2026-11-15부터 상세는 Open API(moelCgmExpc)로 받는다. 저장 행 모양·source_url(원문 화면 주소)은 화면 시절과 같다.
"""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.core import lawgokr_openapi
from scripts.interpretation.logic import scraper as interpretation_scraper
from tests.fakes import FakeDB

DETAIL = "https://www.law.go.kr/LSW/cgmExpcInfoP.do?mode=2&cgmExpcDatSeq=555&ofiClsCd=350101"
BODY = {"안건명": "임금  문의", "해석기관명": "고용노동부", "안건번호": "임금근로시간정책팀-1", "해석일자": "20260901",
        "질의요지": "질문입니다", "회답": "답변입니다<br/>둘째 줄", "이유": ""}


def run(url, body=BODY, db=None, output_dir="/nonexistent", **kwargs):
    db = FakeDB() if db is None else db
    kwargs.setdefault("save_to_db", True)
    calls = []

    def fake_service(target, **params):
        calls.append((target, params))
        if isinstance(body, Exception):
            raise body
        return dict(body)

    with patch.object(lawgokr_openapi, "service", fake_service), \
         patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
        result = asyncio.run(interpretation_scraper.scrape_and_save(
            url=url, output_dir=output_dir, output_name="t", **kwargs))
    return result, db, calls


class InterpretationRowsTest(unittest.TestCase):
    def test_row_schema(self):
        result, db, calls = run({"url": DETAIL, "doc_seq": "555", "ofi_cls_cd": "350101"})
        self.assertEqual(result, {"status": "success", "doc_id": "555", "title": "임금  문의", "sections_count": 2})
        self.assertEqual(calls, [("moelCgmExpc", {"ID": "555"})])
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
        self.assertEqual((second["article_title"], second["content"]), ("회답", "답변입니다\n둘째 줄"))

    def test_resave_updates_in_place_and_keeps_created_at(self):
        _, db, _ = run({"url": DETAIL, "doc_seq": "555"})
        db["interpretation"].docs[0]["metadata"]["created_at"] = "2020-01-01"
        run({"url": DETAIL, "doc_seq": "555"}, db=db)
        self.assertEqual(len(db["interpretation"].docs), 2)
        self.assertEqual(db["interpretation"].docs[0]["metadata"]["created_at"], "2020-01-01")

    def test_doc_seq_is_taken_from_url_when_item_lacks_it(self):
        for url in ({"url": DETAIL}, DETAIL):
            result, db, calls = run(url)
            self.assertEqual(result["doc_id"], "555")
            self.assertEqual(calls[0][1], {"ID": "555"})

    def test_missing_doc_seq_fails_before_any_call(self):
        result, db, calls = run("https://www.law.go.kr/LSW/other.do?x=1")
        self.assertEqual(result, {"status": "failed", "error": "No doc_seq"})
        self.assertEqual((dict(db), calls), ({}, []))

    def test_reason_section_is_kept_when_present(self):
        _, db, _ = run({"url": DETAIL, "doc_seq": "555"}, body=dict(BODY, 이유="이유입니다"))
        self.assertEqual([d["article_title"] for d in db["interpretation"].docs], ["질의요지", "회답", "이유"])

    def test_unparseable_date_falls_back_to_today(self):
        _, db, _ = run({"url": DETAIL, "doc_seq": "555"}, body=dict(BODY, 해석일자=""))
        doc = db["interpretation"].docs[0]
        self.assertEqual(doc["metadata"]["effective"], datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(doc["sub_title"], "고용노동부, 임금근로시간정책팀-1")

    def test_empty_body_is_failed_and_nothing_saved(self):
        result, db, _ = run({"url": DETAIL, "doc_seq": "555"}, body=dict(BODY, 질의요지="", 회답=""))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(dict(db), {})

    def test_api_error_is_failed_and_nothing_saved(self):
        result, db, _ = run({"url": DETAIL, "doc_seq": "555"},
                            body=lawgokr_openapi.OpenApiError("law.go.kr Open API 오류 응답"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("Open API", result["error"])
        self.assertEqual(dict(db), {})

    def test_save_failure_is_reported_as_failed(self):
        db = FakeDB()
        db["interpretation"].fail_update = lambda update, filter_: True
        result, _, _ = run({"url": DETAIL, "doc_seq": "555"}, db=db)
        self.assertEqual((result["status"], result["error"]), ("failed", "MongoDB 저장 실패"))

    def test_partial_save_is_failed_and_marked_for_retry(self):
        db = FakeDB()
        db["interpretation"].fail_update = lambda update, filter_: filter_.get("article_number") == "2"
        result, _, _ = run({"url": DETAIL, "doc_seq": "555"}, db=db)
        self.assertEqual(result["status"], "failed")
        self.assertEqual([d["metadata"].get("save_incomplete") for d in db["interpretation"].docs], [True])

    def test_save_to_db_false_writes_nothing(self):
        result, db, _ = run({"url": DETAIL, "doc_seq": "555"}, save_to_db=False)
        self.assertEqual(result["status"], "success")
        self.assertEqual(dict(db), {})

    def test_jsonl_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _ = run({"url": DETAIL, "doc_seq": "555"}, output_dir=tmp, save_jsonl=True)
            self.assertEqual(result["status"], "success")
            with open(os.path.join(tmp, "t_임금  문의.jsonl"), encoding="utf-8") as f:
                data = json.loads(f.read())
        self.assertEqual(data, {
            "doc_id": "555", "doc_title": "임금 문의", "sub_title": "고용노동부, 임금근로시간정책팀-1, 2026.9.1",
            "department": "고용노동부", "doc_number": "임금근로시간정책팀-1", "doc_date": "2026.9.1",
            "source_url": DETAIL, "sections": {"질의요지": "질문입니다", "회답": "답변입니다\n둘째 줄"}})


if __name__ == "__main__":
    unittest.main()
