"""fix:text-cleanup: 순수 정리 함수와 미리보기/저장 동작."""
import re
import unittest
from argparse import Namespace

from scripts.core.database.source_versioning import build_source_version_id, enrich_source_document, sha256_content
from scripts.migrations import text_cleanup as m
from scripts.migrations.text_cleanup import clean_text


class CleanTextTest(unittest.TestCase):
    def test_broken_middle_dot_only_between_hangul(self):
        self.assertEqual(clean_text("도？소매", "case"), "도ㆍ소매")
        self.assertEqual(clean_text("What？", "case"), "What？")
        self.assertEqual(clean_text("그렇습니까？", "case"), "그렇습니까？")
        self.assertEqual(clean_text("A？가", "case"), "A？가")

    def test_html_traces(self):
        self.assertEqual(clean_text("가<br>나&nbsp;다", "case"), "가\n나 다")
        self.assertEqual(clean_text("가<br/>나<BR />다", "case"), "가\n나\n다")

    def test_law_title_gap_becomes_newline_only_for_law(self):
        text = "제23조(해고 등의 제한)   ① 사용자는 이   조항을"
        self.assertEqual(clean_text(text, "law"), "제23조(해고 등의 제한)\n① 사용자는 이   조항을")
        self.assertEqual(clean_text("제5조의2(정의)  ① 가", "law"), "제5조의2(정의)\n① 가")
        self.assertEqual(clean_text(text, "case"), text)
        self.assertEqual(clean_text("제23조(제목) ① 한 칸", "law"), "제23조(제목) ① 한 칸")

    def test_idempotent(self):
        text = "제23조(해고)   ① 도？소매<br>끝&nbsp;"
        for collection in ("law", "case"):
            once = clean_text(text, collection)
            self.assertEqual(clean_text(once, collection), once)


class FakeCollection:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.bulk_calls = []
        self.unsets = []

    def find(self, query, projection=None):
        assert query == {"metadata.is_active": {"$ne": False}}
        return [dict(r) for r in self.rows if (r.get("metadata") or {}).get("is_active") is not False]

    def bulk_write(self, ops, ordered=False):
        self.bulk_calls.append(ops)
        modified = 0
        for op in ops:
            flt, upd = op._filter, op._doc["$set"]
            for r in self.rows:
                if r["_id"] == flt["_id"] and r["content"] == flt["content"]:
                    r.update(upd)
                    modified += 1
        return type("R", (), {"modified_count": modified})()

    def update_many(self, flt, update):
        self.unsets.append((flt, update))
        n = 0
        for r in self.rows:
            if r.get("doc_id") == flt["doc_id"] and r["metadata"].get("embed_recipe") == flt["metadata.embed_recipe"]:
                r.pop("embedding_hash", None)
                n += 1
        return type("R", (), {"modified_count": n})()


class FakeDb(dict):
    def __getitem__(self, name):
        return self.setdefault(name, FakeCollection([]))


def args(**kw):
    base = dict(collections=["case", "law"], apply=False, samples=3)
    base.update(kw)
    return Namespace(**base)


def make_db():
    db = FakeDb()
    db["case"] = FakeCollection([
        {"_id": 1, "doc_id": "d1", "chunk_id": "d1:0", "content": "도？소매", "content_hash": "old",
         "metadata": {"is_active": True}},
        {"_id": 2, "doc_id": "d1", "chunk_id": "d1:1", "content": "깨끗", "embedding_hash": "h",
         "metadata": {"is_active": True, "embed_recipe": "title+whole@4096"}},
        {"_id": 3, "doc_id": "d2", "chunk_id": "d2:0", "content": "가<br>나", "embedding_hash": "h2",
         "metadata": {"is_active": True, "embed_recipe": "title+whole@4096"}},
        {"_id": 4, "doc_id": "d3", "chunk_id": "d3:0", "content": "도？소매", "metadata": {"is_active": False}},
    ])
    db["law"] = FakeCollection([
        {"_id": 5, "doc_id": "l1", "chunk_id": "l1:23", "content": "제23조(해고)   ① 사용자는",
         "embedding_hash": "x", "metadata": {"is_active": True, "embed_recipe": "title+whole@4096"}},
    ])
    return db


class RunTest(unittest.TestCase):
    def test_preview_does_not_write(self):
        db = make_db()
        lines = []
        totals = m.run(args(), db, out=lines.append)
        self.assertEqual(totals["case"]["changed"], 2)  # 비활성 행 제외
        self.assertEqual(totals["law"]["changed"], 1)
        self.assertEqual(db["case"].bulk_calls, [])
        self.assertEqual(db["case"].unsets, [])
        self.assertEqual(db["case"].rows[0]["content"], "도？소매")
        self.assertTrue(any("미리 보기" in line for line in lines))

    def test_apply_rewrites_content_and_hashes(self):
        db = make_db()
        m.run(args(apply=True), db, out=lambda *_: None)
        row = db["case"].rows[0]
        self.assertEqual(row["content"], "도ㆍ소매")
        self.assertEqual(row["content_hash"], sha256_content("도ㆍ소매"))
        self.assertEqual(row["source_version_id"], build_source_version_id("case", "d1:0", row["content_hash"]))
        self.assertEqual(db["law"].rows[0]["content"], "제23조(해고)\n① 사용자는")

    def test_reembed_only_when_letters_change(self):
        db = make_db()
        m.run(args(apply=True), db, out=lambda *_: None)
        # d1: 글자가 바뀜(？→ㆍ) → 대표 행(_id 2)의 해시 제거
        self.assertNotIn("embedding_hash", db["case"].rows[1])
        # d2: "<br>" 글자가 사라지므로 임베딩 입력이 바뀜 → 제거
        self.assertNotIn("embedding_hash", db["case"].rows[2])
        # law: 공백만 → 유지
        self.assertEqual(db["law"].rows[0]["embedding_hash"], "x")


class SavePathTest(unittest.TestCase):
    def test_enrich_source_document_cleans_content(self):
        doc = {"doc_id": "d", "chunk_id": "d:0", "content": "도？소매<br>끝", "metadata": {}}
        out = enrich_source_document(doc, "case")
        self.assertEqual(out["content"], "도ㆍ소매\n끝")
        self.assertEqual(out["content_hash"], sha256_content(out["content"]))


if __name__ == "__main__":
    unittest.main()
