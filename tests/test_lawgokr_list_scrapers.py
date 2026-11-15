"""법제처 자료 5종 목록(Open API): 항목 모양은 옛 화면 목록과 같고, 일부 실패는 기록하고 나머지를 모은다."""
import unittest
from unittest import mock

from scripts.case.logic import list_scraper as case_list
from scripts.constitutional_decc.logic import list_scraper as detc_list
from scripts.core import lawgokr_openapi as api
from scripts.core.run_report import start_run_report
from scripts.interpretation.logic import list_scraper as interp_list
from scripts.law.logic import list_scraper as law_list
from scripts.legislation_expc.logic import list_scraper as expc_list
from tests.test_lawgokr_openapi import FakeGet, FakeStore, no_retry

LAWS = {"current": ["근로기준법", "최저임금법"], "all": ["근로기준법", "최저임금법"]}


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(api, "call_with_retry", no_retry)
        patcher.start()
        self.addCleanup(patcher.stop)


class LawListTest(Base):
    def test_current_and_upcoming_per_org_keep_old_item_shape(self):
        def route(url, p):
            rows = {("1492000", "3"): [{"법령일련번호": "1", "시행일자": "20260101", "법령명한글": "근로기준법"}],
                    ("1492000", "2"): [{"법령일련번호": "2", "시행일자": "29991231", "법령명한글": "근로기준법"}],
                    ("1790365", "3"): [{"법령일련번호": "3", "시행일자": "20250101", "법령명한글": "개인정보 보호법"}],
                    ("1790365", "2"): []}[(p["org"], p["nw"])]
            return {"LawSearch": {"totalCnt": str(len(rows)), "law": rows}}
        items = law_list.collect_items(get=FakeGet(route))
        self.assertEqual(items[0], {
            "name": "근로기준법", "url": "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=1&efYd=20260101",
            "effective": "20260101", "dept_name": "고용노동부", "law_id": "근로기준법", "is_upcoming": False})
        self.assertEqual([(i["url"][-16:], i["is_upcoming"], i["dept_name"]) for i in items[1:]],
                         [("=2&efYd=29991231", True, "고용노동부"), ("=3&efYd=20250101", False, "개인정보보호위원회")])

    def test_one_failed_org_is_recorded_and_others_kept(self):
        report = start_run_report("law")

        def route(url, p):
            if p["org"] == "1790365":
                raise RuntimeError("down")
            return {"LawSearch": {"totalCnt": "1", "law": [{"법령일련번호": p["nw"], "시행일자": "20260101",
                                                            "법령명한글": "근로기준법"}]}}
        items = law_list.collect_items(get=FakeGet(route))
        self.assertEqual(len(items), 2)
        self.assertEqual(sorted(report.failed_targets),
                         ["law:list-org:1790365:시행예정", "law:list-org:1790365:현행"])


class CaseListTest(Base):
    def test_union_by_referenced_law_without_duplicates(self):
        def route(url, p):
            rows = {"근로기준법": [{"판례일련번호": "10", "사건명": "임금"}, {"판례일련번호": "11", "사건명": "해고"}],
                    "최저임금법": [{"판례일련번호": "10", "사건명": "임금"}]}[p["JO"]]
            return {"PrecSearch": {"totalCnt": str(len(rows)), "prec": rows}}
        items = case_list.collect_items(get=FakeGet(route), laws=LAWS)
        self.assertEqual(sorted(i["url"] for i in items),
                         ["https://www.law.go.kr/LSW/precInfoP.do?mode=0&precSeq=10",
                          "https://www.law.go.kr/LSW/precInfoP.do?mode=0&precSeq=11"])

    def test_failed_law_is_recorded(self):
        report = start_run_report("case")

        def route(url, p):
            if p["JO"] == "최저임금법":
                raise RuntimeError("down")
            return {"PrecSearch": {"totalCnt": "1", "prec": [{"판례일련번호": "10", "사건명": "임금"}]}}
        items = case_list.collect_items(get=FakeGet(route), laws=LAWS)
        self.assertEqual(len(items), 1)
        self.assertEqual(report.failed_targets, ["case:list-law:최저임금법"])


class ScopedListTest(Base):
    def test_detc_and_expc_build_old_detail_urls(self):
        detc_rows = [{"헌재결정례일련번호": "5", "사건명": "근로기준법 제36조 위헌확인"}]
        expc_rows = [{"법령해석례일련번호": "7", "안건명": "민원인 - 「최저임금법」 제5조 관련"}]

        def route(url, p):
            if p["target"] == "detc":
                return {"DetcSearch": {"totalCnt": "1", "Detc": detc_rows}}
            return {"Expc": {"totalCnt": "1", "expc": expc_rows}}
        get = FakeGet(route)
        self.assertEqual(detc_list.collect_items(get=get, laws=LAWS, store=FakeStore()),
                         [{"name": "근로기준법 제36조 위헌확인",
                           "url": "https://www.law.go.kr/LSW/detcInfoP.do?mode=1&detcSeq=5"}])
        self.assertEqual(expc_list.collect_items(get=get, laws=LAWS, store=FakeStore())[0]["url"],
                         "https://www.law.go.kr/LSW/expcInfoP.do?mode=2&expcSeq=7")


class InterpretationListTest(Base):
    def test_items_carry_doc_seq_and_open_on_the_web(self):
        rows = [{"법령해석일련번호": "314616"}, {"법령해석일련번호": "314616"}, {"법령해석일련번호": "2774221"}]
        get = FakeGet(lambda u, p: {"CgmExpc": {"totalCnt": "3", "cgmExpc": rows}})
        items = interp_list.collect_items(get=get)
        self.assertEqual([i["doc_seq"] for i in items], ["314616", "2774221"])
        self.assertEqual(items[0]["url"],
                         "https://www.law.go.kr/LSW/cgmExpcInfoP.do?mode=2&cgmExpcDatSeq=314616&ofiClsCd=350101")
        self.assertEqual(get.calls[0][1]["target"], "moelCgmExpc")

    def test_failed_middle_page_is_recorded_and_others_kept(self):
        report = start_run_report("interpretation")

        def route(url, p):
            if p["page"] == 2:
                raise RuntimeError("timeout")
            rows = [{"법령해석일련번호": str(p["page"] * 1000 + i)} for i in range(100 if p["page"] == 1 else 3)]
            return {"CgmExpc": {"totalCnt": "203", "cgmExpc": rows}}
        items = interp_list.collect_items(get=FakeGet(route))
        self.assertEqual(len(items), 103)
        self.assertEqual(report.failed_targets, ["interpretation:list-page:2"])

    def test_error_body_raises_so_the_listing_fails(self):
        get = FakeGet(lambda u, p: {"CgmExpc": "점검 중"})
        with self.assertRaises(api.OpenApiError):
            interp_list.collect_items(get=get)


if __name__ == "__main__":
    unittest.main()
