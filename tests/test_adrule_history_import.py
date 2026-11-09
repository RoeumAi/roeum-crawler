"""행정규칙 과거 버전 넣기·고시일 채우기 — 계획(확정 고시만·종료일·다음 버전)과 상세 API 값 해석."""
import types
import unittest

from scripts.adrule import history_import as hi
from scripts.core.lawgokr_stable_id import adrule_basic_meta
from scripts.migrations import adrule_record_meta as arm


def row(doc_id, title, promulgated, effective, number, adrule_id="2002374"):
    return {"행정규칙일련번호": doc_id, "행정규칙ID": adrule_id, "행정규칙명": title,
            "발령일자": promulgated, "시행일자": effective, "발령번호": number}


# law.go.kr 연혁 검색(2026-11-09) 결과 일부 — 최저임금 고시(행정규칙ID 2002374)
ROWS = [
    row("2100000227560", "2024년 적용 최저임금 고시", "20230804", "20240101", "2023-43"),
    row("2100000226812", "2024년 적용 최저임금안 고시", "20230720", "20240101", "2023-42"),
    row("2100000245668", "2025년 적용 최저임금 고시", "20240805", "20250101", "2024-50"),
    row("2100000244478", "2025년 적용 최저임금안 고시", "20240717", "20250101", "2024-45"),
    row("2100000262710", "2026년 적용 최저임금 고시", "20250805", "20260101", "2025-47"),
    row("2100000283564", "2027년 적용 최저임금 고시", "20260805", "20270101", "2026-60"),
    row("2100000269386", "2026년 선원 최저임금 고시", "20251208", "20260101", "2025-200", adrule_id="2002396"),
]
CURRENT = {"2100000262710", "2100000283564"}  # adrule 에 있는 현행·시행예정


class PlanHistoryTest(unittest.TestCase):
    def test_only_final_notices_of_the_same_rule_before_the_current(self):
        planned = hi.plan_history(ROWS, "2002374", CURRENT)
        self.assertEqual(["2100000227560", "2100000245668"], [v["doc_id"] for v in planned])

    def test_effective_to_is_the_day_before_the_next_version(self):
        planned = {v["doc_id"]: v for v in hi.plan_history(ROWS, "2002374", CURRENT)}
        v2025 = planned["2100000245668"]
        self.assertEqual("2025-01-01", v2025["effective"])
        self.assertEqual("2025-12-31", v2025["effective_to"])
        self.assertEqual("2100000262710", v2025["superseded_by"])
        self.assertEqual("2024-08-05", v2025["promulgation_date"])
        self.assertEqual("2024-50", v2025["promulgation_number"])
        # 다음 버전이 안 고시가 아니라 확정 고시다(같은 시행일의 안 고시는 계획에서 빠진다)
        self.assertEqual("2100000245668", planned["2100000227560"]["superseded_by"])

    def test_latest_version_is_left_to_the_daily_crawl(self):
        planned = hi.plan_history(ROWS, "2002374", existing_doc_ids=set())
        self.assertNotIn("2100000283564", [v["doc_id"] for v in planned])
        self.assertIn("2100000262710", [v["doc_id"] for v in planned])

    def test_since_year_and_existing_versions_are_skipped(self):
        planned = hi.plan_history(ROWS, "2002374", CURRENT | {"2100000227560"}, since_year=2025)
        self.assertEqual(["2100000245668"], [v["doc_id"] for v in planned])

    def test_history_meta_marks_rows(self):
        v = hi.plan_history(ROWS, "2002374", CURRENT)[-1]
        self.assertEqual({"history": True, "is_upcoming": False, "effective_to": "2025-12-31",
                          "superseded_by": "2100000262710"}, hi.history_meta(v))


RECORD = {"AdmRulService": {"행정규칙기본정보": {
    "발령일자": "20250805", "발령번호": "2025-47", "소관부처명": "고용노동부", "제개정구분명": "제정",
    "행정규칙종류": "고시", "시행일자": "20260101"}}}


class BasicMetaTest(unittest.TestCase):
    def test_reads_promulgation_and_department(self):
        self.assertEqual({"promulgation_date": "2025-08-05", "promulgation_number": "2025-47",
                          "dept_name": "고용노동부", "revision_kind": "제정", "rule_kind": "고시"},
                         adrule_basic_meta(RECORD))

    def test_missing_or_malformed_values_are_left_out(self):
        self.assertEqual({}, adrule_basic_meta(None))
        bad = {"AdmRulService": {"행정규칙기본정보": {"발령일자": "2025.8.5", "발령번호": " "}}}
        self.assertEqual({}, adrule_basic_meta(bad))


class FakeCollection:
    def __init__(self, doc_ids):
        self.doc_ids = doc_ids
        self.written = []

    def distinct(self, field, flt):
        return list(self.doc_ids)

    def bulk_write(self, ops, ordered=False):
        self.written.extend(ops)
        return types.SimpleNamespace(modified_count=len(ops))


class RecordMetaBackfillTest(unittest.TestCase):
    def run_tool(self, apply):
        db = {"adrule": FakeCollection(["2100000262710", "999"]), "adrule_history": FakeCollection([])}
        fetch = lambda target, doc_id: RECORD if doc_id == "2100000262710" else None  # noqa: E731
        args = types.SimpleNamespace(apply=apply, limit=0, samples=0)
        return arm.run(args, db, fetch=fetch, out=lambda *_: None), db

    def test_preview_writes_nothing(self):
        stat, db = self.run_tool(apply=False)
        self.assertEqual({"docs": 2, "updated_docs": 1, "no_record": 1, "written": 0}, stat)
        self.assertEqual([], db["adrule"].written)

    def test_apply_sets_only_record_fields_on_rows_missing_them(self):
        stat, db = self.run_tool(apply=True)
        self.assertEqual(1, stat["written"])
        op = db["adrule"].written[0]
        self.assertEqual("2100000262710", op._filter["doc_id"])
        self.assertEqual({"$exists": False}, op._filter["metadata.promulgation_date"])
        self.assertEqual("2025-08-05", op._doc["$set"]["metadata.promulgation_date"])
        self.assertEqual("고용노동부", op._doc["$set"]["metadata.dept_name"])


if __name__ == "__main__":
    unittest.main()


class SaveMetaTest(unittest.TestCase):
    """저장 행 metadata 에 고시일·고시번호·소관부처가 실리고, 과거 버전은 adrule_history 에 표시와 함께 저장된다."""

    def save(self, **kwargs):
        from unittest.mock import patch

        from scripts.adrule.logic import scraper

        captured = {}

        def fake_save_articles(repo, articles, build_doc, **_):
            captured["collection"] = repo.collection_name
            captured["doc"] = build_doc(1, articles[0], len(articles))
            return "insert"

        class Repo:
            def __init__(self, db, collection_name):
                self.collection_name = collection_name

        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=object()), \
             patch.object(scraper, "UnifiedDocumentRepository", Repo), \
             patch.object(scraper, "collect_annex_articles", return_value=([], False)), \
             patch.object(scraper, "save_articles", side_effect=fake_save_articles):
            scraper.save_to_mongodb([{"title": "", "text": "1. 최저임금액 10,320원"}], "2026년 적용 최저임금 고시",
                                    "2100000262710", "u", dept_code="1492000", effective_date="2026-01-01",
                                    adrule_id="2002374", lawgokr_record=RECORD, **kwargs)
        return captured

    def test_current_rows_carry_record_fields(self):
        captured = self.save()
        meta = captured["doc"]["metadata"]
        self.assertEqual("adrule", captured["collection"])
        self.assertEqual("2025-08-05", meta["promulgation_date"])
        self.assertEqual("2025-47", meta["promulgation_number"])
        self.assertEqual("고용노동부", meta["dept_name"])
        self.assertNotIn("history", meta)

    def test_history_rows_go_to_their_own_collection(self):
        captured = self.save(collection_name="adrule_history",
                             extra_meta={"history": True, "effective_to": "2026-12-31"})
        self.assertEqual("adrule_history", captured["collection"])
        self.assertTrue(captured["doc"]["metadata"]["history"])
        self.assertEqual("2026-12-31", captured["doc"]["metadata"]["effective_to"])
