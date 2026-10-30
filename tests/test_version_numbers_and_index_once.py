"""버전 번호가 2에서 멈추지 않는다 / 저장소를 문서마다 만들어도 인덱스는 컬렉션당 한 번만 만든다."""
import unittest

from scripts.core.database.change_detector import VersionManager
from scripts.core.database.unified_repository import UnifiedDocumentRepository
from tests.fakes import FakeDB


def article(content, number="1"):
    return {"doc_id": "L1", "doc_type": "법령", "article_number": number, "title": "법", "sub_title": "",
            "content": content, "metadata": {"source_url": "u", "effective": "2026-01-01", "is_active": True}}


class VersionNumberTest(unittest.TestCase):
    def test_versions_keep_counting_up(self):
        db = FakeDB()
        versions = []
        for content in ("v1 본문", "v2 본문", "v3 본문", "v4 본문"):
            _, result = UnifiedDocumentRepository(db).upsert_with_change_detection(article(content))
            versions.append(result["version"])
        self.assertEqual(versions, [1, 2, 3, 4])
        active = [d for d in db["law"].docs if d["metadata"]["is_active"]]
        self.assertEqual([(d["content"], d["metadata"]["version"]) for d in active], [("v4 본문", 4)])

    def test_unchanged_resave_keeps_version(self):
        db = FakeDB()
        for content in ("본문", "본문 수정", "본문 수정"):
            _, result = UnifiedDocumentRepository(db).upsert_with_change_detection(article(content))
        self.assertEqual(result["action"], "update_existing")
        self.assertEqual(result["version"], 2)

    def test_legacy_rows_with_only_current_version_continue_from_it(self):
        legacy = {"metadata": {"current_version": 2}}
        self.assertEqual(VersionManager.version_of(legacy), 2)
        self.assertEqual(VersionManager.prepare_version_update(legacy, article("x"), {})["version"], 3)
        self.assertEqual(VersionManager.prepare_metadata_update(legacy, article("x"))["version"], 2)
        self.assertEqual(VersionManager.version_of(None), 1)


class IndexOnceTest(unittest.TestCase):
    def test_indexes_are_created_once_per_collection(self):
        db = FakeDB()
        for _ in range(5):
            UnifiedDocumentRepository(db, collection_name="decision")
        first = db["decision"].index_calls
        self.assertGreater(first, 0)
        UnifiedDocumentRepository(db, collection_name="decision")
        self.assertEqual(db["decision"].index_calls, first)

    def test_each_collection_gets_its_own_indexes(self):
        db = FakeDB()
        UnifiedDocumentRepository(db, collection_name="decision")
        UnifiedDocumentRepository(db, collection_name="mediation_case")
        self.assertGreater(db["mediation_case"].index_calls, 0)

    def test_failed_index_creation_is_retried_next_time(self):
        db = FakeDB()

        def broken(*args, **kwargs):
            raise RuntimeError("down")
        db["law"].create_index = broken
        UnifiedDocumentRepository(db)
        db["law"].create_index = lambda *a, **k: setattr(db["law"], "index_calls", db["law"].index_calls + 1)
        UnifiedDocumentRepository(db)
        self.assertGreater(db["law"].index_calls, 0)


if __name__ == "__main__":
    unittest.main()
