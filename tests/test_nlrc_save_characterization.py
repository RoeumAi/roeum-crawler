"""판정사례(judgment)·조정사건례(mediation_case) 문서 구성과 저장 결과를 고정하는 특성 테스트.

두 컬렉션은 저장 방식이 다르다(judgment 는 단순 $set, mediation_case 는 버전 관리 저장소).
묶어 정리하더라도 그 차이와 각자의 결과가 그대로여야 한다.
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from scripts.judgment.logic import scraper as judgment_scraper
from scripts.mediation_case.logic import scraper as mediation_scraper
from tests.fakes import FakeDB
from tests.test_nlrc_pdf_scrapers import JUDGMENT_HTML, MEDIATION_HTML, extracted_pdf

J_URL = "https://nlrc.go.kr/nlrc/mainCase/judgment/detail.do?jgmtSn=11&jgmtDcsnSeCd=65"
M_URL = "https://nlrc.go.kr/nlrc/mainCase/mediatioin/detail.do?jgmtSn=22&jgmtDcsnSeCd=66"
FAILED_WITH_ATTACHMENT = {
    "success": False, "text": "", "content_source": "html_fallback",
    "attachment": {"name": "첨부.pdf", "file_id": "65_65_1", "download_url": "https://nlrc.go.kr/d.pdf"},
    "page_count": None, "is_searchable": None, "cost_usd": 0.0, "error": "extract API timeout",
}
NO_ATTACHMENT = {**FAILED_WITH_ATTACHMENT, "attachment": None, "error": "Supported attachment not found"}

KINDS = {
    "judgment": dict(module=judgment_scraper, html=JUDGMENT_HTML, url=J_URL, doc_id="11",
                     doc_type="주요판정사례", title="판정 제목", body="판정사항: 판정사항 요약\n\n판정요지: 판정요지 요약"),
    "mediation_case": dict(module=mediation_scraper, html=MEDIATION_HTML, url=M_URL, doc_id="22",
                           doc_type="조정사건례", title="조정 제목", body="조정 요약"),
}


def run(kind, db=None, pdf=None, html=None, url=None, **kwargs):
    spec = KINDS[kind]
    module = spec["module"]
    db = FakeDB() if db is None else db
    kwargs.setdefault("save_jsonl", False)
    with patch.object(module, "_fetch_detail_html", return_value=html or spec["html"]), \
         patch.object(module, "extract_attachment_text", return_value=pdf or NO_ATTACHMENT), \
         patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
        result = asyncio.run(module.scrape_and_save(url or spec["url"], output_dir="", output_name="t", **kwargs))
    return result, db


def active(db, kind):
    return [d for d in db[kind].docs if d.get("metadata", {}).get("is_active", True)]


class NlrcDocumentTest(unittest.TestCase):
    def each(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                yield kind, KINDS[kind]

    def test_document_fields_and_metadata_keys(self):
        for kind, spec in self.each():
            result, db = run(kind)
            self.assertEqual((result["doc_id"], result["status"]), (spec["doc_id"], "success"))
            row = db[kind].docs[0]
            self.assertEqual(row["chunk_id"], f"{kind}:{spec['doc_id']}")
            self.assertEqual((row["doc_id"], row["doc_type"], row["article_number"], row["title"]),
                             (spec["doc_id"], spec["doc_type"], "1", spec["title"]))
            self.assertEqual(row["content"], spec["body"])
            self.assertEqual(row["sub_title"], "")
            meta = row["metadata"]
            self.assertEqual(meta["source_url"], spec["url"])
            self.assertEqual((meta["source_type"], meta["is_active"], meta["effective"]), ("web", True, "2026-07-28"))
            self.assertEqual((meta["pdf_retry_needed"], meta["pdf_retry_count"]), (False, 0))
            self.assertEqual(meta["content_source"], "html_fallback")
            self.assertEqual(meta["pdf_error"], "Supported attachment not found")
            self.assertEqual(meta["attachment_names"], [])
            base = {"source_url", "source_type", "created_at", "updated_at", "is_active", "effective",
                    "attachment_name", "attachment_url", "attachment_file_id", "attachment_names",
                    "attachment_file_ids", "content_source", "pdf_page_count", "pdf_extraction_status",
                    "pdf_is_searchable", "pdf_cost_usd", "pdf_error", "pdf_retry_needed", "pdf_retry_count"}
            self.assertTrue(base <= set(meta))
            if kind == "judgment":  # 단순 $set 이라 문서 구성 키 그대로다
                self.assertEqual(set(meta), base)

    def test_attachment_text_is_appended_after_summary(self):
        for kind, spec in self.each():
            _, db = run(kind, pdf=extracted_pdf("첨부 본문"))
            row = db[kind].docs[0]
            self.assertEqual(row["content"], f"{spec['body']}\n\n[첨부파일 전문]\n첨부 본문")
            self.assertEqual(row["metadata"]["attachment_name"], "첨부.pdf")
            self.assertEqual(row["metadata"]["attachment_file_ids"], [])
            self.assertEqual(row["metadata"]["pdf_page_count"], 3)

    def test_jsonl_document_output(self):
        for kind, spec in self.each():
            with tempfile.TemporaryDirectory() as tmp:
                module = spec["module"]
                with patch.object(module, "_fetch_detail_html", return_value=spec["html"]), \
                     patch.object(module, "extract_attachment_text", return_value=NO_ATTACHMENT):
                    asyncio.run(module.scrape_and_save(spec["url"], output_dir=tmp, output_name="o",
                                                       save_to_db=False, save_jsonl=True))
                with open(os.path.join(tmp, "o_document.jsonl"), encoding="utf-8") as f:
                    data = json.loads(f.read())
            self.assertEqual((data["doc_id"], data["title"], data["content"]), (spec["doc_id"], spec["title"], spec["body"]))

    def test_save_to_db_false_writes_nothing(self):
        for kind, spec in self.each():
            result, db = run(kind, save_to_db=False)
            self.assertEqual(result["status"], "success")
            self.assertEqual(dict(db), {})

    def test_empty_url_and_fetch_error(self):
        for kind, spec in self.each():
            self.assertEqual(asyncio.run(spec["module"].scrape_and_save({"url": ""}, "", "t")),
                             {"doc_id": None, "status": "failed"})
            with patch.object(spec["module"], "_fetch_detail_html", side_effect=RuntimeError("down")):
                result = asyncio.run(spec["module"].scrape_and_save(spec["url"], "", "t", save_jsonl=False))
            self.assertEqual(result, {"doc_id": None, "status": "failed", "error": "RuntimeError: down"})

    def test_retry_count_grows_on_repeated_failures(self):
        for kind, spec in self.each():
            db = FakeDB()
            counts = []
            for attempt, text in enumerate(["첫 본문", "둘째 본문", "셋째 본문"]):
                html = spec["html"].replace("요약", f"요약{attempt}")
                run(kind, db=db, html=html, pdf=FAILED_WITH_ATTACHMENT)
                meta = active(db, kind)[0]["metadata"]
                counts.append((meta["pdf_retry_count"], meta["pdf_retry_needed"]))
            # 첫 저장은 새 행이라 직접 세고, 이후 실패 재수집은 옛 본문을 지키는 경로가 센다. 둘 다 3회째에 재시도를 끈다.
            self.assertEqual(counts, [(1, True), (2, True), (3, False)])
            # 성공하면 0 으로 돌아간다
            run(kind, db=db, html=spec["html"].replace("요약", "요약성공"), pdf=extracted_pdf("본문"))
            meta = active(db, kind)[0]["metadata"]
            self.assertEqual((meta["pdf_retry_count"], meta["pdf_retry_needed"]), (0, False))

    def test_retry_stops_after_limit_and_keeps_old_text_visible_as_failed(self):
        """첨부 추출이 계속 실패해도 매일 밤 같은 첨부를 다시 추출하지 않는다(무한 재시도 방지)."""
        for kind, spec in self.each():
            db = FakeDB()
            run(kind, db=db, pdf=extracted_pdf("전문 본문"))
            old_content = active(db, kind)[0]["content"]
            seen = []
            for night in range(6):
                run(kind, db=db, pdf=FAILED_WITH_ATTACHMENT)
                meta = active(db, kind)[0]["metadata"]
                seen.append((meta["pdf_retry_count"], meta["pdf_retry_needed"]))
            # 3회째에 표시가 꺼지고 그 뒤로는 update 모드가 이 문서를 다시 고르지 않는다(꺼진 채 유지).
            self.assertEqual(seen[:3], [(1, True), (2, True), (3, False)])
            self.assertTrue(all(needed is False for _, needed in seen[3:]))
            row = active(db, kind)[0]
            self.assertEqual(row["content"], old_content)
            self.assertEqual(row["metadata"]["pdf_last_attempt_status"], "failed")


class JudgmentStorageTest(unittest.TestCase):
    def test_actions_new_unchanged_changed(self):
        result, db = run("judgment")
        self.assertEqual(result["action"], "new_version")
        result, db = run("judgment", db=db)
        self.assertEqual(result["action"], "no_change")
        result, db = run("judgment", db=db, html=JUDGMENT_HTML.replace("판정요지 요약", "다른 요지"))
        self.assertEqual(result["action"], "update_existing")
        self.assertEqual(len(db["judgment"].docs), 1)
        self.assertNotIn("_action", db["judgment"].docs[0])
        self.assertIn("다른 요지", db["judgment"].docs[0]["content"])

    def test_plain_set_replaces_metadata_wholesale(self):
        _, db = run("judgment")
        row = db["judgment"].docs[0]
        row["metadata"]["embedding_status"] = "done"
        row["metadata"]["created_at"] = "2020-01-01T00:00:00"
        run("judgment", db=db)
        meta = db["judgment"].docs[0]["metadata"]
        self.assertNotIn("embedding_status", meta)
        self.assertNotEqual(meta["created_at"], "2020-01-01T00:00:00")

    def test_failed_refresh_keeps_old_text_and_counts_retry(self):
        _, db = run("judgment", pdf=extracted_pdf("전문 본문"))
        old = db["judgment"].docs[0]["content"]
        result, db = run("judgment", db=db, pdf=FAILED_WITH_ATTACHMENT)
        self.assertEqual(result["action"], "no_change")
        row = db["judgment"].docs[0]
        self.assertEqual(row["content"], old)
        self.assertEqual((row["metadata"]["pdf_retry_needed"], row["metadata"]["pdf_retry_count"]), (True, 1))

    def test_write_failure_is_failed(self):
        db = FakeDB()
        db["judgment"].fail_update = lambda update, filter_: True
        result, _ = run("judgment", db=db)
        self.assertEqual(result, {"doc_id": "11", "status": "failed"})

    def test_missing_jgmt_sn_falls_back_to_title(self):
        result, db = run("judgment", url="https://nlrc.go.kr/x.do?a=1")
        self.assertEqual(result["doc_id"], "판정 제목")
        self.assertEqual(db["judgment"].docs[0]["doc_id"], "판정 제목")


class MediationStorageTest(unittest.TestCase):
    def test_versioned_storage_and_result_has_no_action(self):
        result, db = run("mediation_case")
        self.assertEqual(result, {"doc_id": "22", "status": "success"})
        row = db["mediation_case"].docs[0]
        self.assertEqual(row["metadata"]["current_version"], 1)
        result, db = run("mediation_case", db=db)
        self.assertEqual(len(active(db, "mediation_case")), 1)
        run("mediation_case", db=db, html=MEDIATION_HTML.replace("조정 요약", "다른 요약"))
        rows = db["mediation_case"].docs
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(d["metadata"]["current_version"] for d in rows), [1, 2])
        self.assertEqual([d["content"] for d in active(db, "mediation_case")], ["다른 요약"])

    def test_missing_jgmt_sn_fails(self):
        result, db = run("mediation_case", url="https://nlrc.go.kr/x.do?a=1")
        self.assertEqual(result, {"doc_id": None, "status": "failed"})

    def test_failed_refresh_keeps_old_text_and_counts_retry(self):
        _, db = run("mediation_case", pdf=extracted_pdf("전문 본문"))
        old = active(db, "mediation_case")[0]["content"]
        result, db = run("mediation_case", db=db, pdf=FAILED_WITH_ATTACHMENT)
        self.assertEqual(result["status"], "success")
        row = active(db, "mediation_case")[0]
        self.assertEqual(row["content"], old)
        self.assertEqual((row["metadata"]["pdf_retry_needed"], row["metadata"]["pdf_retry_count"]), (True, 1))

    def test_repository_creation_failure_is_failed(self):
        with patch("scripts.core.database.mongo_client.get_mongo_db", side_effect=RuntimeError("down")):
            self.assertFalse(mediation_scraper.save_mediation_to_mongodb({"doc_id": "1", "metadata": {}}))


if __name__ == "__main__":
    unittest.main()
