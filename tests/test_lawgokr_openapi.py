import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

from scripts.core import lawgokr_openapi as api


def fake_response(payload, status=200):
    resp = SimpleNamespace(status_code=status)
    resp.raise_for_status = lambda: None
    resp.json = (lambda: payload) if not isinstance(payload, Exception) else (lambda: (_ for _ in ()).throw(payload))
    return resp


class FakeGet:
    """params 로 응답을 고르는 가짜 requests.get. 부른 params 를 calls 에 남긴다."""

    def __init__(self, route):
        self.route = route
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params)))
        return fake_response(self.route(url, dict(params)))


def no_retry(fn, *a, **k):
    return fn()


class SearchTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(api, "call_with_retry", no_retry)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pages_until_total_and_sends_oc_json(self):
        def route(url, p):
            n = 100 if p["page"] == 1 else 30
            return {"PrecSearch": {"totalCnt": "130", "prec": [{"판례일련번호": f"{p['page']}-{i}"} for i in range(n)]}}
        get = FakeGet(route)
        rows = list(api.search("prec", {"JO": "근로기준법"}, get=get))
        self.assertEqual(len(rows), 130)
        self.assertEqual([c[1]["page"] for c in get.calls], [1, 2])
        self.assertEqual(get.calls[0][1]["type"], "JSON")
        self.assertIn("OC", get.calls[0][1])

    def test_capitalised_row_key_and_single_row_dict(self):
        get = FakeGet(lambda u, p: {"DetcSearch": {"totalCnt": "1", "Detc": {"헌재결정례일련번호": "1"}}})
        self.assertEqual(list(api.search("detc", get=get)), [{"헌재결정례일련번호": "1"}])

    def test_error_text_body_raises_open_api_error(self):
        get = FakeGet(lambda u, p: {"Law": "일치하는 법령해석이 없습니다."})
        with self.assertRaises(api.OpenApiError) as ctx:
            api.service("moelCgmExpc", get=get, ID="1")
        self.assertIn("Open API", str(ctx.exception))

    def test_missing_total_keeps_paging_while_pages_are_full(self):
        def route(url, p):
            n = 100 if p["page"] < 3 else 5
            return {"PrecSearch": {"prec": [{"판례일련번호": f"{p['page']}-{i}"} for i in range(n)]}}
        self.assertEqual(len(list(api.search("prec", get=FakeGet(route)))), 205)

    def test_middle_page_failure_is_reported_and_paging_continues(self):
        failed = []

        def route(url, p):
            if p["page"] == 2:
                raise RuntimeError("timeout")
            n = 100 if p["page"] < 3 else 10
            return {"PrecSearch": {"totalCnt": "210", "prec": [{"id": f"{p['page']}-{i}"} for i in range(n)]}}
        rows = list(api.search("prec", get=FakeGet(route), on_page_error=lambda pg, e: failed.append(pg)))
        self.assertEqual((len(rows), failed), (110, [2]))

    def test_first_page_failure_always_raises(self):
        get = FakeGet(lambda u, p: (_ for _ in ()).throw(RuntimeError("down")))
        with self.assertRaises(RuntimeError):
            list(api.search("prec", get=get, on_page_error=lambda pg, e: None))

    def test_html_page_raises_open_api_error(self):
        get = lambda url, params=None, timeout=None: fake_response(ValueError("no json"))
        with self.assertRaises(api.OpenApiError):
            list(api.search("law", get=get))


class ServiceRetryTest(unittest.TestCase):
    def test_not_found_is_not_retried_but_network_error_is(self):
        from unittest.mock import patch as _patch
        calls = {"n": 0}

        def get(url, params=None, timeout=None):
            calls["n"] += 1
            return fake_response({"Law": "일치하는 법령해석이 없습니다."})
        with _patch("scripts.core.run_report.time.sleep") as sleep:
            with self.assertRaises(api.OpenApiError):
                api.service("moelCgmExpc", get=get, ID="1")
        self.assertEqual((calls["n"], sleep.call_count), (1, 0))


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.m = api.LawNameMatcher(["근로기준법", "노동위원회법", "파견근로자보호등에관한법률"])

    def test_exact_name_and_particles(self):
        self.assertTrue(self.m.cites("「근로기준법」 제36조 위헌확인"))
        self.assertTrue(self.m.cites("근로기준법상 근로자"))
        self.assertTrue(self.m.cites("구 근로기준법 제2조"))

    def test_name_inside_longer_law_name_does_not_count(self):
        self.assertFalse(self.m.cites("경제사회노동위원회법 제2조"))

    def test_spacing_differences_are_ignored(self):
        self.assertTrue(self.m.cites("「파견근로자보호 등에 관한 법률」 제6조의2"))

    def test_unrelated_text(self):
        self.assertFalse(self.m.cites("도로교통법 부칙 제6조 위헌소원"))
        self.assertFalse(api.LawNameMatcher([]).cites("근로기준법"))


class FakeStore:
    def __init__(self, known=None):
        self.known = known or {}
        self.saved = {}

    def load(self, ids):
        return {i: self.known[i] for i in ids if i in self.known}

    def save(self, decisions):
        self.saved.update(decisions)


class ScopedCandidatesTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(api, "call_with_retry", no_retry)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_scoped(self, store):
        rows = {
            "1": {"헌재결정례일련번호": "1", "사건명": "근로기준법 제36조 위헌확인"},       # 제목으로 통과
            "2": {"헌재결정례일련번호": "2", "사건명": "기본권 침해 위헌확인"},            # 상세로 통과
            "3": {"헌재결정례일련번호": "3", "사건명": "도로교통법 부칙 위헌소원"},          # 상세로 탈락
            "4": {"헌재결정례일련번호": "4", "사건명": "기소유예처분취소"},                # 기록으로 통과
        }
        details = {"2": {"심판대상조문": "근로기준법 제2조"}, "3": {"심판대상조문": "도로교통법 부칙"}}

        def route(url, p):
            if url == api.SEARCH_URL:
                return {"DetcSearch": {"totalCnt": "4", "Detc": list(rows.values())}}
            return {"DetcService": details[p["ID"]]}

        get = FakeGet(route)
        kept = api.scoped_candidates(
            target="detc", id_key="헌재결정례일련번호", title_key="사건명",
            detail_fields=("사건명", "심판대상조문", "참조조문"), collection_name="constitutional_decc",
            laws={"current": ["근로기준법"], "all": ["근로기준법"]}, store=store, get=get, max_workers=1)
        return sorted(r["헌재결정례일련번호"] for r in kept), get

    def test_title_detail_and_recorded_decisions(self):
        store = FakeStore(known={"4": True})
        kept, get = self.run_scoped(store)
        self.assertEqual(kept, ["1", "2", "4"])
        self.assertEqual(store.saved, {"2": True, "3": False})
        detail_ids = sorted(p["ID"] for url, p in get.calls if url == api.SERVICE_URL)
        self.assertEqual(detail_ids, ["2", "3"])  # 제목 일치·기록 있는 문서는 상세를 묻지 않는다

    def test_search_failure_is_reported_and_others_continue(self):
        failures = []
        get = FakeGet(lambda u, p: (_ for _ in ()).throw(RuntimeError("down")) if p.get("search") == "2"
                      else {"DetcSearch": {"totalCnt": "1", "Detc": [{"헌재결정례일련번호": "1", "사건명": "근로기준법 위헌"}]}})
        kept = api.scoped_candidates(
            target="detc", id_key="헌재결정례일련번호", title_key="사건명", detail_fields=("사건명",),
            collection_name="constitutional_decc", laws={"current": ["근로기준법"], "all": ["근로기준법"]},
            store=FakeStore(), get=get, on_failure=lambda t, e: failures.append(t), max_workers=1)
        self.assertEqual([r["헌재결정례일련번호"] for r in kept], ["1"])
        self.assertEqual(failures, ["list-query:근로기준법"])

    def test_failed_detail_check_is_reported_not_recorded(self):
        failures = []

        def route(url, p):
            if url == api.SERVICE_URL:
                raise RuntimeError("down")
            return {"DetcSearch": {"totalCnt": "1", "Detc": [{"헌재결정례일련번호": "9", "사건명": "기본권 침해"}]}}
        store = FakeStore()
        kept = api.scoped_candidates(
            target="detc", id_key="헌재결정례일련번호", title_key="사건명", detail_fields=("사건명",),
            collection_name="constitutional_decc", laws={"current": ["근로기준법"], "all": ["근로기준법"]},
            store=store, get=FakeGet(route), on_failure=lambda t, e: failures.append(t), max_workers=1)
        self.assertEqual((kept, store.saved), ([], {}))  # 판정 못 한 문서는 기록하지 않아 다음 날 다시 본다
        self.assertEqual(sorted(set(failures)), ["scope-check:9"])


class JurisdictionLawsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(api, "call_with_retry", no_retry)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_current_and_history_names(self):
        def route(url, p):
            if p["target"] == "law":
                return {"LawSearch": {"totalCnt": "1", "law": [{"법령명한글": f"현행{p['org']}"}]}}
            return {"LawSearch": {"totalCnt": "1", "law": [{"법령명한글": "옛이름법"}]}}
        laws = api.jurisdiction_laws(get=FakeGet(route))
        self.assertEqual(laws["current"], ["현행1492000"])  # 범위는 고용노동부 소관만
        self.assertIn("옛이름법", laws["all"])

    def test_empty_current_list_raises(self):
        get = FakeGet(lambda u, p: {"LawSearch": {"totalCnt": "0"}})
        with self.assertRaises(api.OpenApiError):
            api.jurisdiction_laws(get=get)


if __name__ == "__main__":
    unittest.main()
