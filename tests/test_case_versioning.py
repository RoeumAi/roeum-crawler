import unittest
from unittest.mock import MagicMock, patch


def _apply_dotted_set(doc, set_fields):
    for key, value in set_fields.items():
        if "." in key:
            top, sub = key.split(".", 1)
            doc.setdefault(top, {})
            doc[top][sub] = value
        else:
            doc[key] = value


class FakeCaseCollection:
    def __init__(self, initial_docs):
        self.docs = initial_docs

    def _match(self, filter_, doc):
        for k, v in filter_.items():
            if k == "metadata.is_active" and isinstance(v, dict) and "$ne" in v:
                if doc.get("metadata", {}).get("is_active") == v["$ne"]:
                    return False
                continue
            if doc.get(k) != v:
                return False
        return True

    def find_one(self, filter_, projection=None):
        for d in self.docs:
            if self._match(filter_, d):
                return d
        return None

    def find(self, filter_, projection=None):
        return [d for d in self.docs if self._match(filter_, d)]

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if self._match(filter_, d):
                if "$set" in update:
                    _apply_dotted_set(d, update["$set"])
                return
        if upsert:
            new_doc = dict(filter_)
            if "$set" in update:
                _apply_dotted_set(new_doc, update["$set"])
            if "$setOnInsert" in update:
                _apply_dotted_set(new_doc, update["$setOnInsert"])
            self.docs.append(new_doc)


class CaseVersioningTest(unittest.TestCase):
    def _run_save(self, initial_docs, chunks):
        from scripts.case.logic import scraper as case_scraper

        collection = FakeCaseCollection(initial_docs)
        fake_db = {"case": collection}

        with patch(
            "scripts.core.database.mongo_client.get_mongo_db",
            return_value=fake_db,
        ):
            case_scraper.save_case_chunks_to_mongodb(
                base_doc_id="622053",
                doc_title="새 판례 제목",
                doc_subtitle="대법원 2026다12345",
                url="https://www.law.go.kr/LSW/precInfoP.do?precSeq=622053",
                chunks=chunks,
            )
        return collection

    def test_deactivates_chunk_missing_from_latest_crawl(self):
        initial_docs = [
            {
                "doc_id": "622053",
                "doc_type": "판시사항",
                "chunk_seq": 1,
                "content_hash": "old-hash",
                "metadata": {"is_active": True},
            },
            {
                "doc_id": "622053",
                "doc_type": "참조판례",
                "chunk_seq": 1,
                "content_hash": "ref-hash",
                "metadata": {"is_active": True},
            },
        ]
        chunks = [{"title": "판시사항", "text": "완전히 새로운 내용", "metadata": {}}]

        collection = self._run_save(initial_docs, chunks)

        by_key = {(d["doc_type"], d["chunk_seq"]): d for d in collection.docs}
        self.assertFalse(by_key[("참조판례", 1)]["metadata"]["is_active"])
        self.assertIn("deactivated_at", by_key[("참조판례", 1)]["metadata"])
        self.assertTrue(by_key[("판시사항", 1)]["metadata"]["is_active"])

    def test_no_change_branch_refreshes_title(self):
        from scripts.core.database.source_versioning import sha256_content

        content = "동일한 본문"
        initial_docs = [
            {
                "doc_id": "622053",
                "doc_type": "판시사항",
                "chunk_seq": 1,
                "content_hash": sha256_content(content),
                "title": "옛날 제목",
                "metadata": {"is_active": True},
            },
        ]
        chunks = [{"title": "판시사항", "text": content, "metadata": {}}]

        collection = self._run_save(initial_docs, chunks)

        self.assertEqual(collection.docs[0]["title"], "새 판례 제목")


if __name__ == "__main__":
    unittest.main()
