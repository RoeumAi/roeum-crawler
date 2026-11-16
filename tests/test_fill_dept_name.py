"""빠진 기관명 채우기 도구: 컬렉션별 값·빈 행만·본문과 updated_at 은 그대로."""
import unittest
from types import SimpleNamespace

from scripts.migrations import fill_dept_name as tool
from tests.fakes import FakeDB


def row(doc_id, sub_title="", dept=None, **meta):
    m = {"updated_at": "2026-10-01", **meta}
    if dept is not None:
        m["dept_name"] = dept
    return {"doc_id": doc_id, "sub_title": sub_title, "content": "본문", "metadata": m}


class FakeBulkDB(FakeDB):
    """bulk_write(UpdateMany) 를 update_many 로 풀어 주는 가짜 DB."""

    def __missing__(self, name):
        coll = super().__missing__(name)

        def bulk_write(ops, ordered=False):
            n = 0
            for op in ops:
                n += coll.update_many(op._filter, op._doc).modified_count
            return SimpleNamespace(modified_count=n)
        coll.bulk_write = bulk_write
        return coll


class FillDeptNameTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeBulkDB()
        self.db["constitutional_decc"].docs += [row("1"), row("2", dept="헌법재판소")]
        self.db["legislation_expc"].docs += [row("3")]
        self.db["admin_decc"].docs += [row("4", "국민권익위원회 2000-01715, 2000. 5. 8., 인용"),
                                       row("5", "경기도행정심판위원회 2017-982, 2017. 7. 31."), row("6", "")]
        self.db["mediation_case"].docs += [row("7", dept="중앙노동위원회", is_active=False), row("7"), row("8")]
        self.db["adrule"].docs += [row("9", dept="노동위원회"), row("9"), row("10")]
        self.db["case"].docs += [row("11"), row("12", dept="고용노동부")]
        self.db["judgment"].docs += [row("13", "노동위원회(중앙) 2008. 9. 4. 판정 2008부해486"),
                                     row("14", "지방노동위원회(서울) 2026. 7. 14. 판정 2026부해1628"),
                                     row("15", "2025. 8. 11. 판정")]
        for i, r in enumerate(self.db["case"].docs + self.db["judgment"].docs):
            r["_id"] = f"id{i}"

    def run_tool(self, apply):
        fetch = lambda target, doc_id: {"AdmRulService": {"행정규칙기본정보": {"소관부처명": "고용노동부"}}}
        return tool.run(SimpleNamespace(apply=apply), self.db, fetch=fetch, out=lambda *a: None)

    def dept(self, name):
        return [(r["doc_id"], r["metadata"].get("dept_name")) for r in self.db[name].docs]

    def test_preview_writes_nothing(self):
        stat = self.run_tool(apply=False)
        self.assertEqual(stat["admin_decc"]["빈 행"], 3)
        self.assertEqual(self.dept("legislation_expc"), [("3", None)])

    def test_apply_fills_per_collection_rules(self):
        self.run_tool(apply=True)
        self.assertEqual(self.dept("constitutional_decc"), [("1", "헌법재판소"), ("2", "헌법재판소")])
        self.assertEqual(self.dept("legislation_expc"), [("3", "법제처")])
        self.assertEqual(self.dept("admin_decc"), [("4", "국민권익위원회"), ("5", "경기도행정심판위원회"), ("6", None)])
        self.assertEqual(self.dept("mediation_case"), [("7", "중앙노동위원회"), ("7", "중앙노동위원회"), ("8", "중앙노동위원회")])
        self.assertEqual(self.dept("adrule"), [("9", "노동위원회"), ("9", "노동위원회"), ("10", "고용노동부")])
        self.assertEqual(self.dept("case"), [("11", "고용노동부"), ("12", "고용노동부")])
        self.assertEqual(self.dept("judgment"), [("13", "중앙노동위원회"), ("14", "서울지방노동위원회"), ("15", None)])

    def test_content_and_updated_at_untouched(self):
        self.run_tool(apply=True)
        for name in tool.COLLECTIONS:
            for r in self.db[name].docs:
                self.assertEqual((r["content"], r["metadata"]["updated_at"]), ("본문", "2026-10-01"))


if __name__ == "__main__":
    unittest.main()
