"""pdf-reparse: 미리 보기는 쓰지 않고, 저장 때는 ID·벡터를 지키며 문서 단위로 바꾼다."""
import tempfile
import unittest
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts.pdf_ingest import reparse

OLD_TEXT = "예전에 일부만 읽힌 본문 " * 20


class FakeCollection:
    def __init__(self, rows):
        self.rows = {r["_id"]: r for r in rows}
        self.replaced = []

    def distinct(self, field, query):
        def ok(r):
            m = r["metadata"]
            return (m.get("source_type") == "pdf" and m.get("is_active")
                    and (query.get("metadata.pdf_retry_needed") is None or m.get("pdf_retry_needed"))
                    and ("doc_id" not in query or r["doc_id"] in query["doc_id"]["$in"]))
        return sorted({r[field] for r in self.rows.values() if ok(r)})

    def find(self, query):
        return [dict(r) for r in self.rows.values() if r["doc_id"] == query["doc_id"]]

    def replace_one(self, filt, row, session=None):
        self.replaced.append(row)
        self.rows[filt["_id"]] = row


class FakeClient:
    @contextmanager
    def start_session(self):
        class Session:
            def with_transaction(self, fn):
                fn(self)
        yield Session()


def rows(recipe):
    meta = {"source_type": "pdf", "is_active": True, "pdf_retry_needed": True, "pdf_extraction_status": "partial",
            "source_file": "감독 1-1.pdf", "is_searchable": True, "embed_recipe": recipe}
    return [{"_id": "a", "doc_id": "d1", "chunk_id": "c1", "chunk_seq": 1, "title": "제목", "sub_title": "",
             "content": OLD_TEXT, "embedding": [0.1, 0.2], "embedding_hash": "old", "metadata": dict(meta)}]


def extracted(text):
    return {"is_success": True, "extraction_status": "complete", "full_text": text, "structured_text": text,
            "document_blocks": [{"kind": "text", "text": text}, {"kind": "table", "cells": []}],
            "extraction_metadata": {"paragraph_projection": {"status": "complete"}}}


class ReparseTest(unittest.TestCase):
    def run_reparse(self, collection, apply, text, type_name="행정해석"):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "감독 1-1.pdf").write_bytes(b"%PDF")
        dirs = {type_name: (tmp, "interpretation")}
        args = Namespace(type=type_name, doc_id=[], limit=0, include_complete=False, apply=apply)
        lines = []
        with patch.object(reparse, "PDF_DIRS", dirs), patch.object(reparse, "TYPE_COLLECTION", {type_name: "interpretation"}):
            totals = reparse.reparse(args, {"interpretation": collection}, FakeClient(),
                                     extract=lambda data, name: extracted(text), out=lines.append)
        return totals, "\n".join(lines)

    def test_dry_run_writes_nothing(self):
        collection = FakeCollection(rows(reparse.WHOLE_DOCUMENT_RECIPE))
        totals, log = self.run_reparse(collection, apply=False, text="새로 다 읽힌 본문 " * 25)
        self.assertEqual(collection.replaced, [])
        self.assertEqual(totals["to_complete"], 1)
        self.assertIn("partial → complete", log)
        self.assertIn("표 0 → 1", log)

    def test_apply_keeps_ids_and_vector_and_marks_whole_doc_for_reembedding(self):
        collection = FakeCollection(rows(reparse.WHOLE_DOCUMENT_RECIPE))
        totals, _ = self.run_reparse(collection, apply=True, text="새로 다 읽힌 본문 " * 25)
        self.assertEqual(totals["written"], 1)
        row = collection.replaced[0]
        self.assertEqual((row["_id"], row["chunk_id"]), ("a", "c1"))
        self.assertEqual(row["embedding"], [0.1, 0.2])        # 교체 전까지 기존 벡터로 검색된다
        self.assertNotIn("embedding_hash", row)              # 03:30 갱신이 다시 임베딩
        self.assertEqual(row["metadata"]["pdf_extraction_status"], "complete")
        self.assertFalse(row["metadata"]["pdf_retry_needed"])

    def test_per_row_vectors_keep_hash_so_content_hash_change_triggers_refresh(self):
        collection = FakeCollection(rows("title+sub_title+content@4096"))
        self.run_reparse(collection, apply=True, text="새로 다 읽힌 본문 " * 25)
        self.assertEqual(collection.replaced[0]["embedding_hash"], "old")

    def test_major_text_loss_keeps_existing_document(self):
        collection = FakeCollection(rows(reparse.WHOLE_DOCUMENT_RECIPE))
        totals, log = self.run_reparse(collection, apply=True, text="짧음")
        self.assertEqual(collection.replaced, [])
        self.assertEqual(totals["retained"], 1)
        self.assertIn("major_text_loss", log)

    def test_nfd_file_name_is_found(self):
        import unicodedata
        tmp = Path(tempfile.mkdtemp())
        (tmp / unicodedata.normalize("NFD", "감독 1-1.pdf")).write_bytes(b"%PDF")
        self.assertIn("감독 1-1.pdf", reparse.file_index(tmp))


if __name__ == "__main__":
    unittest.main()
