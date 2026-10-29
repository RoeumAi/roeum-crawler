"""fix:split-paragraphs: 저장 때 content_hash·source_version_id 도 다시 계산한다."""
import unittest
from argparse import Namespace

from scripts.core.database.source_versioning import build_source_version_id, sha256_content
from scripts.migrations import split_flat_paragraphs as m

FLAT = "원고의 청구를 기각하였다. 2. 판단 피고는 다투었다. 가. 법리 법리는 이렇다. " * 20


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def find(self, query, projection=None):
        return [dict(r) for r in self.rows]

    def bulk_write(self, ops, ordered=False):
        self.calls += 1
        for op in ops:
            for r in self.rows:
                if r["_id"] == op._filter["_id"] and r["content"] == op._filter["content"]:
                    r.update(op._doc["$set"])
        return type("R", (), {"modified_count": len(ops)})()


def args(apply):
    return Namespace(collection="case", source_type="csv", limit=0, samples=0, apply=apply)


class SplitHashTest(unittest.TestCase):
    def test_apply_recomputes_hashes(self):
        col = FakeCollection([{"_id": 1, "doc_id": "d", "chunk_id": "d:0", "content": FLAT, "content_hash": "old"}])
        m.run(args(True), {"case": col}, out=lambda *_: None)
        row = col.rows[0]
        self.assertIn("\n\n", row["content"])
        self.assertEqual(row["content_hash"], sha256_content(row["content"]))
        self.assertEqual(row["source_version_id"], build_source_version_id("case", "d:0", row["content_hash"]))

    def test_preview_writes_nothing(self):
        col = FakeCollection([{"_id": 1, "doc_id": "d", "content": FLAT}])
        m.run(args(False), {"case": col}, out=lambda *_: None)
        self.assertEqual(col.calls, 0)


if __name__ == "__main__":
    unittest.main()
