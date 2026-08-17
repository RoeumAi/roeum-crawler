import unittest

from scripts.core.database.source_versioning import (
    build_source_version_id,
    sha256_content,
)
from scripts.migrations.backfill_clean_pdf_artifacts import (
    plan_collection_cleanup,
    plan_document_update,
)


HRDB_DOC = {
    "_id": "x1",
    "chunk_id": "interpretation:doc1",
    "content": (
        "육아휴직기간을 호봉승급기간에서 제외해도 타당한지\n"
        "26. 7. 3. 오후 3:14\n"
        "https://hrdb.kr/search/print?wm_id=1&pdf=1\n"
        "1/1"
    ),
    "content_hash": "stale-hash",
    "metadata": {"source_type": "pdf", "is_searchable": True},
}


class PlanDocumentUpdateTest(unittest.TestCase):
    def test_hrdb_doc_plans_clean_update_with_recomputed_hash(self):
        kind, set_fields = plan_document_update(HRDB_DOC, "interpretation")

        expected_content = "육아휴직기간을 호봉승급기간에서 제외해도 타당한지"
        expected_hash = sha256_content(expected_content)
        self.assertEqual(kind, "clean")
        self.assertEqual(set_fields["content"], expected_content)
        self.assertEqual(set_fields["content_hash"], expected_hash)
        self.assertEqual(
            set_fields["source_version_id"],
            build_source_version_id("interpretation", "interpretation:doc1", expected_hash),
        )

    def test_already_clean_doc_plans_nothing(self):
        doc = {
            "_id": "x2",
            "chunk_id": "law:1",
            "content": "남녀고용평등법 제11조는 강행규정으로서",
            "content_hash": sha256_content("남녀고용평등법 제11조는 강행규정으로서"),
            "metadata": {},
        }

        self.assertIsNone(plan_document_update(doc, "law"))

    def test_stub_doc_plans_flag_without_touching_content(self):
        doc = {
            "_id": "x3",
            "chunk_id": "constitutional_decc:1",
            "content": "제목: 고용보험법 제23조 위헌소원\n출처: https://www.law.go.kr/x",
            "metadata": {"is_searchable": True},
        }

        kind, set_fields = plan_document_update(doc, "constitutional_decc")

        self.assertEqual(kind, "stub")
        self.assertNotIn("content", set_fields)
        self.assertEqual(set_fields["metadata.is_stub"], True)
        self.assertEqual(set_fields["metadata.is_searchable"], False)

    def test_already_flagged_stub_plans_nothing(self):
        doc = {
            "_id": "x4",
            "chunk_id": "constitutional_decc:1",
            "content": "제목: 어떤 것\n출처: https://x",
            "metadata": {"is_stub": True, "is_searchable": False},
        }

        self.assertIsNone(plan_document_update(doc, "constitutional_decc"))


class PlanCollectionCleanupTest(unittest.TestCase):
    def test_collects_only_changed_documents(self):
        clean_doc = {
            "_id": "c1",
            "chunk_id": "law:1",
            "content": "정상 본문",
            "metadata": {},
        }
        docs = [HRDB_DOC, clean_doc]

        plans = plan_collection_cleanup(docs, "interpretation")

        self.assertEqual(len(plans), 1)
        _id, kind, set_fields = plans[0]
        self.assertEqual(_id, "x1")
        self.assertEqual(kind, "clean")


if __name__ == "__main__":
    unittest.main()
