import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from scripts.migrations.judgment_decision_date import new_sub_title, run


def row(**over):
    base = {
        "_id": over.get("doc_id", "1"),
        "doc_id": "1",
        "title": "판정 제목",
        "sub_title": "",
        "content": "",
        "metadata": {"effective": "2026-06-11"},
    }
    return {**base, **over}


class NewSubTitleTest(unittest.TestCase):
    def test_adds_date_to_stored_case_number(self):
        doc = row(sub_title="중앙2018부해303", content="판 정 일 2018. 5. 25. 우리 위원회는",
                  metadata={"effective": "2018-08-15"})
        self.assertEqual(new_sub_title(doc), "중앙노동위원회 2018. 5. 25. 판정 2018부해303")

    def test_date_only_when_no_number(self):
        doc = row(content="사례 3 [2026. 3. 23. 판정 / 초심: 기각]")
        self.assertEqual(new_sub_title(doc), "2026. 3. 23. 판정")

    def test_fills_case_number_missed_in_title(self):
        doc = row(title="분리 필요성 있음[중앙2018단위3]", metadata={"effective": "2018-05-17"})
        self.assertEqual(new_sub_title(doc), "중앙2018단위3")

    def test_keeps_stored_number_without_date(self):
        doc = row(sub_title="중앙2025부해749", content="주문과 같이 판정한다.")
        self.assertEqual(new_sub_title(doc), "중앙2025부해749")

    def test_idempotent_when_date_exists(self):
        stored = "중앙노동위원회 2018. 5. 25. 판정 2018부해303"
        self.assertEqual(new_sub_title(row(sub_title=stored, content="판정일 2010. 1. 1.")), stored)


class RunTest(unittest.TestCase):
    def _db(self, rows):
        collection = MagicMock()
        collection.find.return_value = rows
        collection.bulk_write.return_value = MagicMock(modified_count=len(rows))
        return {"judgment": collection}, collection

    def test_preview_never_writes(self):
        db, collection = self._db([row(content="사례 3 [2026. 3. 23. 판정 /")])
        stat = run(SimpleNamespace(apply=False, samples=0), db, out=lambda _: None)
        collection.bulk_write.assert_not_called()
        self.assertEqual(stat["changed"], 1)
        self.assertEqual(stat["written"], 0)

    def test_apply_sets_only_sub_title(self):
        db, collection = self._db([row(content="사례 3 [2026. 3. 23. 판정 /")])
        run(SimpleNamespace(apply=True, samples=0), db, out=lambda _: None)
        op = collection.bulk_write.call_args.args[0][0]
        self.assertEqual(op._doc, {"$set": {"sub_title": "2026. 3. 23. 판정"}})


if __name__ == "__main__":
    unittest.main()
