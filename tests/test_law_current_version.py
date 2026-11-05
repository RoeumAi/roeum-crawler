import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.law.logic import scraper as law_scraper
from scripts.core.database.refresh_promotion import (
    group_active_documents_by_stable_id,
    plan_group_transitions,
)
from scripts.law.logic.version_doc_id import resolve_version_doc_id, version_doc_id
from scripts.migrations.law_current_version import (
    find_versions_to_restore,
    plan_version_splits,
    rows_to_restore,
    run,
    split_update,
)

TODAY = "2026-11-05"


def row(doc_id, effective, *, active, upcoming=False, article="1", _id=None, **meta):
    return {"_id": _id or f"{doc_id}-{effective}-{article}", "doc_id": doc_id, "article_number": article,
            "chunk_id": f"law:{doc_id}:article:{article}",
            "metadata": {"law_id": "L", "effective": effective, "is_active": active, "is_upcoming": upcoming, **meta}}


class FakeCollection:
    """조건에 맞는 첫 행을 돌려준다. `$ne` 만 해석한다."""

    def __init__(self, rows):
        self.rows = rows
        self.written = []

    @staticmethod
    def _get(row, dotted):
        value = row
        for part in dotted.split("."):
            value = (value or {}).get(part)
        return value

    def find_one(self, query, projection=None):
        for candidate in self.rows:
            if all(self._get(candidate, k) != v["$ne"] if isinstance(v, dict) else self._get(candidate, k) == v
                   for k, v in query.items()):
                return candidate
        return None

    def find(self, query, projection=None):
        return list(self.rows)

    def bulk_write(self, ops, ordered=False):
        self.written += ops
        return SimpleNamespace(modified_count=len(ops))


class ResolveVersionDocIdTest(unittest.TestCase):
    URL = "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=287805&efYd=20270108"

    def test_first_version_keeps_lsi_seq(self):
        self.assertEqual(resolve_version_doc_id(FakeCollection([]), "287805", self.URL, "2027-01-08"), "287805")

    def test_second_effective_of_same_promulgation_gets_own_doc_id(self):
        collection = FakeCollection([row("287805", "2026-07-07", active=True)])
        self.assertEqual(resolve_version_doc_id(collection, "287805", self.URL, "2027-01-08"), "287805-20270108")

    def test_recrawl_reuses_saved_doc_id(self):
        saved = row("287805-20270108", "2027-01-08", active=True, upcoming=True, source_url=self.URL)
        collection = FakeCollection([row("287805", "2026-07-07", active=True), saved])
        self.assertEqual(resolve_version_doc_id(collection, "287805", self.URL, "2027-01-08"), "287805-20270108")

    def test_undated_version_keeps_lsi_seq(self):
        collection = FakeCollection([row("287805", "2026-07-07", active=True)])
        self.assertEqual(resolve_version_doc_id(collection, "287805", None, "99991231"), "287805")


class LawSaveUsesVersionDocIdTest(unittest.TestCase):
    def test_upcoming_is_saved_apart_and_current_is_not_touched(self):
        current = row("287805", "2026-07-07", active=True)
        repo = Mock()
        repo.collection = FakeCollection([current])
        repo.upsert_with_change_detection.return_value = ("x", {"action": "insert"})
        doc = {"doc_id": "287805", "title": "산업안전보건법", "sub_title": "부제", "content": "[제1조(목적)]\n목적",
               "is_upcoming": True,
               "metadata": {"source_url": "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=287805&efYd=20270108",
                            "effective": "2027-01-08", "is_active": True}}
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=Mock()), \
             patch.object(law_scraper, "UnifiedDocumentRepository", return_value=repo), \
             patch.object(law_scraper, "fetch_record", return_value={}) as fetch:
            law_scraper.save_to_mongodb(doc)
        fetch.assert_called_once_with("law", "287805")  # 외부 API 는 공포본 번호로 조회한다
        saved = repo.upsert_with_change_detection.call_args.args[0]
        self.assertEqual(saved["doc_id"], "287805-20270108")
        self.assertEqual(saved["chunk_id"], "law:287805-20270108:article:1")


class StagedPromotionTest(unittest.TestCase):
    """버전마다 doc_id 가 다르면 기존 승격 로직이 시행일 순서대로 하나씩 넘긴다."""

    docs = [row("287805", "2026-07-07", active=True),
            row("283449", "2027-01-01", active=True, upcoming=True),
            row(version_doc_id("287805", "2027-01-08"), "2027-01-08", active=True, upcoming=True)]

    def plan(self, today):
        return plan_group_transitions(group_active_documents_by_stable_id(self.docs, "law_id")["L"], today)

    def test_keeps_current_before_next_effective(self):
        plan = self.plan(TODAY)
        self.assertFalse(plan["promoted"])
        self.assertEqual(plan["deactivate"], [])

    def test_promotion_retires_only_old_version(self):
        plan = self.plan("2027-01-01")
        self.assertEqual(plan["activate_current"], "283449")
        self.assertEqual(plan["deactivate"], ["287805"])
        self.assertIn("287805-20270108", plan["keep_upcoming"])


class PlanVersionSplitsTest(unittest.TestCase):
    def test_current_keeps_doc_id_and_upcoming_moves(self):
        rows = [row("287805", "2026-07-07", active=False), row("287805", "2027-01-08", active=True, upcoming=True)]
        self.assertEqual(plan_version_splits(rows, TODAY),
                         [{"doc_id": "287805", "effective": "2027-01-08", "new_doc_id": "287805-20270108"}])

    def test_latest_of_two_active_currents_keeps_doc_id(self):
        rows = [row("284455", "2026-07-01", active=True), row("284455", "2026-09-18", active=True)]
        self.assertEqual(plan_version_splits(rows, TODAY),
                         [{"doc_id": "284455", "effective": "2026-07-01", "new_doc_id": "284455-20260701"}])

    def test_single_effective_is_untouched(self):
        self.assertEqual(plan_version_splits([row("1", "2026-01-01", active=True)], TODAY), [])

    def test_split_update_renames_every_key(self):
        source = row("287805", "2027-01-08", active=True, parent_chunk_id="law:287805:article:별표1")
        self.assertEqual(split_update(source, "287805-20270108"), {
            "doc_id": "287805-20270108", "chunk_id": "law:287805-20270108:article:1",
            "metadata.parent_chunk_id": "law:287805-20270108:article:별표1"})


class FindVersionsToRestoreTest(unittest.TestCase):
    def test_restores_latest_retired_current_sharing_doc_id_with_upcoming(self):
        rows = [row("287805", "2026-07-07", active=False), row("100", "2025-01-01", active=False),
                row("287805", "2027-01-08", active=True, upcoming=True)]
        self.assertEqual(find_versions_to_restore(rows, TODAY),
                         [{"group_id": "L", "doc_id": "287805", "effective": "2026-07-07"}])

    def test_skips_group_with_active_current(self):
        rows = [row("290781", "2026-10-02", active=True), row("283457", "2026-08-20", active=False),
                row("285279", "2027-01-01", active=True, upcoming=True)]
        self.assertEqual(find_versions_to_restore(rows, TODAY), [])

    def test_skips_retired_version_that_does_not_share_doc_id(self):
        rows = [row("100", "2025-01-01", active=False), row("200", "2027-01-01", active=True, upcoming=True)]
        self.assertEqual(find_versions_to_restore(rows, TODAY), [])

    def test_rows_exclude_retired_annex_parts_and_stop_on_duplicates(self):
        target = {"doc_id": "1", "effective": "2026-01-01"}
        part = row("1", "2026-01-01", active=False, article="2", search_only=True)
        keep = row("1", "2026-01-01", active=False)
        self.assertEqual(rows_to_restore([keep, part], target), [keep])
        with self.assertRaises(ValueError):
            rows_to_restore([keep, row("1", "2026-01-01", active=False, _id="other")], target)


class RunTest(unittest.TestCase):
    def rows(self):
        return [row("287805", "2026-07-07", active=False, update_summary={"article_number": "1"}),
                row("287805", "2027-01-08", active=True, upcoming=True)]

    def test_preview_writes_nothing(self):
        collection = FakeCollection(self.rows())
        stat = run(SimpleNamespace(apply=False, backup=None), {"law": collection}, out=lambda _: None, today=TODAY)
        self.assertEqual((stat["split_rows"], stat["restore_rows"], stat["written"]), (1, 1, 0))
        self.assertEqual(collection.written, [])

    def test_apply_backs_up_then_splits_and_restores(self):
        collection = FakeCollection(self.rows())
        with tempfile.TemporaryDirectory() as folder:
            backup = Path(folder) / "rows.json"
            run(SimpleNamespace(apply=True, backup=str(backup)), {"law": collection}, out=lambda _: None, today=TODAY)
            saved = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual([item["action"] for item in saved], ["split", "restore"])
        self.assertEqual(saved[1]["update_summary"], {"article_number": "1"})
        self.assertEqual(len(collection.written), 2)

    def test_apply_with_nothing_to_do_keeps_existing_backup(self):
        collection = FakeCollection([row("1", "2026-01-01", active=True)])
        with tempfile.TemporaryDirectory() as folder:
            backup = Path(folder) / "rows.json"
            run(SimpleNamespace(apply=True, backup=str(backup)), {"law": collection}, out=lambda _: None, today=TODAY)
            self.assertFalse(backup.exists())


if __name__ == "__main__":
    unittest.main()
