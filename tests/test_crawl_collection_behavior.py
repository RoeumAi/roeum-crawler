"""crawl.py 가 컬렉션마다 다르게 하는 일을 고정한다: 목록 시작 URL, update 모드 필터 전략,
재수집 대상 조회 필터, 결과 JSON 모양, 실패 대상 표기, 시작 간격·순서.

가짜 스크래퍼 모듈과 가짜 컬렉션만 쓴다(네트워크·실제 DB 없음)."""
import asyncio
import json
import os
import tempfile
import types
import unittest
from unittest.mock import patch

import crawl
from scripts.core.config import get_scraper_list

ALL_SCRAPERS = [
    "law", "adrule", "case", "decision", "interpretation", "mediation_case",
    "judgment", "constitutional_decc", "legislation_expc", "admin_decc",
]

EXPECTED_START_URLS = {
    "law": "https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd=1492000",
    "adrule": "https://www.law.go.kr/LSW/admRulAstSc.do?menuId=391&subMenuId=397&tabMenuId=441&cptOfiCd=1492000",
    "case": "https://www.law.go.kr/LSW/precAstSc.do?menuId=391&subMenuId=397&tabMenuId=443&cptOfiCd=1492000",
    "decision": "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do",
    "interpretation": "https://www.law.go.kr/cgmExpcAstSc.do?menuId=391&subMenuId=397&tabMenuId=741&query=",
    "mediation_case": "https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do",
    "judgment": "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do",
    "constitutional_decc": "https://www.law.go.kr/LSW/detcAstScListR.do",
    "legislation_expc": "https://www.law.go.kr/LSW/expcAstScListR.do",
    "admin_decc": "https://www.law.go.kr/LSW/deccAstScListR.do",
}

PDF_RETRY = {"metadata.is_active": True, "metadata.pdf_retry_needed": True}
METADATA_ONLY = {"metadata.is_active": True, "metadata.is_metadata_only": True}
SAVE_INCOMPLETE = {"metadata.is_active": True, "metadata.save_incomplete": True}

# 컬렉션 → get_all_crawled_urls 가 doc_id 집합에서 빼는 재수집 필터(순서 포함)
EXPECTED_RETRY_FILTERS = {
    "judgment": [PDF_RETRY],
    "mediation_case": [PDF_RETRY],
    "case": [METADATA_ONLY, SAVE_INCOMPLETE],
    "admin_decc": [METADATA_ONLY, SAVE_INCOMPLETE],
    "constitutional_decc": [METADATA_ONLY, SAVE_INCOMPLETE],
    "legislation_expc": [METADATA_ONLY, SAVE_INCOMPLETE],
    "interpretation": [SAVE_INCOMPLETE],
    "decision": [],
    "law": [],
    "adrule": [],
}


def fake_import(calls, fetch_urls, scrape_and_save):
    def importer(name):
        calls.append(name)
        if name.endswith(".list_scraper"):
            return types.SimpleNamespace(fetch_urls=fetch_urls)
        return types.SimpleNamespace(scrape_and_save=scrape_and_save)
    return importer


def run_scraper(scraper, fetch_urls, scrape_and_save, mode="full", max_pages=None, calls=None):
    with patch.object(crawl, "import_module", fake_import(calls if calls is not None else [], fetch_urls, scrape_and_save)), \
         patch("os.getcwd", return_value=tempfile.gettempdir()):
        return asyncio.run(crawl.run_single_scraper(scraper, 2, max_pages, mode))


class RecordingCollection:
    """distinct 질의를 기록하고, 질의별로 정해 둔 doc_id 를 돌려준다."""

    def __init__(self, answers=None):
        self.queries = []
        self.answers = answers or {}

    def distinct(self, field, query=None):
        query = query or {}
        self.queries.append(query)
        extra = {k: v for k, v in query.items() if k != "doc_id"}
        return list(self.answers.get(json.dumps(extra, sort_keys=True), []))


class ScraperRegistryTest(unittest.TestCase):
    def test_every_scraper_is_covered_by_these_tests(self):
        self.assertEqual(sorted(get_scraper_list()), sorted(ALL_SCRAPERS))

    def test_module_paths_follow_collection_name(self):
        async def fetch_urls(start_url, max_pages_arg):
            return []
        for scraper in ALL_SCRAPERS:
            calls = []
            run_scraper(scraper, fetch_urls, None, calls=calls)
            self.assertEqual(calls, [f"scripts.{scraper}.logic.list_scraper", f"scripts.{scraper}.logic.scraper"])

    def test_list_start_url_and_max_pages_per_collection(self):
        for scraper in ALL_SCRAPERS:
            seen = {}

            async def fetch_urls(start_url, max_pages_arg):
                seen.update(start_url=start_url, max_pages_arg=max_pages_arg)
                return []
            run_scraper(scraper, fetch_urls, None, max_pages=7)
            self.assertEqual(seen, {"start_url": EXPECTED_START_URLS[scraper], "max_pages_arg": 7}, scraper)

    def test_unknown_scraper_raises_before_anything_runs(self):
        with self.assertRaises(ValueError):
            asyncio.run(crawl.run_single_scraper("nope", 1, None, "full"))


class UpdateStrategyTest(unittest.TestCase):
    """update 모드에서 컬렉션이 어떤 필터를 쓰는지."""

    def _update(self, scraper, urls):
        async def fetch_urls(start_url, max_pages_arg):
            return urls

        async def scrape(url, **kwargs):
            return {"status": "success", "doc_id": "d", "action": "insert"}

        used = []

        def effective(name, url_list=None):
            used.append(("effective", name, url_list))
            return {"https://x/a": "20240101"}

        def crawled(name, url_items=None):
            used.append(("crawled", name, url_items))
            return {"1"}

        with patch.object(crawl, "get_crawled_effective_dates", effective), \
             patch.object(crawl, "get_all_crawled_urls", crawled):
            result = run_scraper(scraper, fetch_urls, scrape, mode="update")
        return result, used

    def test_law_and_adrule_use_effective_date_map(self):
        for scraper in ("law", "adrule"):
            urls = [{"url": "https://x/a", "effective": "2024-01-01"},
                    {"url": "https://x/b", "effective": "20240101"},
                    {"url": "https://x/c", "effective": "99991231"}]
            result, used = self._update(scraper, urls)
            # 목록의 URL 만 조회 대상으로 넘긴다.
            self.assertEqual(used, [("effective", scraper, ["https://x/a", "https://x/b", "https://x/c"])])
            # a: 같은 시행일 → 건너뜀, b: 신규, c: 신규(map 에 없음)
            self.assertEqual(result["total_urls"], 3)
            self.assertEqual(result["skipped"], 1)
            self.assertEqual(result["total_success"], 2)

    def test_effective_lookup_skips_falsy_items_and_accepts_str_items(self):
        _, used = self._update("law", ["https://x/a", None, {"url": "https://x/b"}])
        self.assertEqual(used[0][2], ["https://x/a", "https://x/b"])

    def test_other_collections_use_doc_id_set(self):
        for scraper in ALL_SCRAPERS:
            if scraper in ("law", "adrule"):
                continue
            urls = [{"url": "https://x/d?detcSeq=1"}, {"url": "https://x/d?detcSeq=2"}]
            result, used = self._update(scraper, urls)
            self.assertEqual(used, [("crawled", scraper, urls)], scraper)
            self.assertEqual((result["skipped"], result["total_success"]), (1, 1), scraper)

    def test_nothing_new_returns_early_success_with_listing_failures_counted(self):
        async def fetch_urls(start_url, max_pages_arg):
            from scripts.core.run_report import record_failure
            record_failure("list-page:3", "boom")
            return [{"url": "https://x/d?precSeq=1"}]

        async def scrape(url, **kwargs):
            raise AssertionError("신규 항목이 없으면 상세 수집을 하지 않는다")

        with patch.object(crawl, "get_all_crawled_urls", lambda name, url_items=None: {"1"}):
            result = run_scraper("case", fetch_urls, scrape, mode="update")
        self.assertEqual(result, {
            "status": "success", "total_urls": 1, "total_success": 0, "total_failed": 1,
            "skipped": 1, "failed_targets": ["case:list-page:3"], "failure_reasons": {"기타 오류": 1},
        })

    def test_full_mode_never_consults_the_database(self):
        async def fetch_urls(start_url, max_pages_arg):
            return [{"url": "https://x/d?precSeq=1"}]

        async def scrape(url, **kwargs):
            return {"status": "success", "doc_id": "1"}

        boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB 조회 금지"))
        with patch.object(crawl, "get_all_crawled_urls", boom), patch.object(crawl, "get_crawled_effective_dates", boom):
            result = run_scraper("case", fetch_urls, scrape, mode="full")
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["total_success"], 1)


class RetryFilterTest(unittest.TestCase):
    def test_retry_filters_applied_per_collection(self):
        for name, expected in EXPECTED_RETRY_FILTERS.items():
            collection = RecordingCollection()
            with patch("scripts.core.database.mongo_client.get_mongo_db", return_value={name: collection}):
                crawl.get_all_crawled_urls(name, url_items=[{"url": "https://x?precSeq=5"}])
            extras = [{k: v for k, v in q.items() if k != "doc_id"} for q in collection.queries]
            self.assertEqual(extras, [{}] + expected, name)

    def test_retry_docs_are_removed_from_crawled_set(self):
        answers = {
            "{}": ["1", "2", "3"],
            json.dumps(METADATA_ONLY, sort_keys=True): ["2"],
            json.dumps(SAVE_INCOMPLETE, sort_keys=True): ["3"],
        }
        collection = RecordingCollection(answers)
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value={"case": collection}):
            self.assertEqual(crawl.get_all_crawled_urls("case", url_items=[{"url": "u?precSeq=1"}]), {"1"})

    def test_unscoped_lookup_uses_plain_distinct(self):
        collection = RecordingCollection({"{}": ["9"]})
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value={"decision": collection}):
            self.assertEqual(crawl.get_all_crawled_urls("decision"), {"9"})
        self.assertEqual(collection.queries, [{}])

    def test_filter_helpers(self):
        self.assertEqual(crawl.get_retry_doc_id_filter("judgment"), PDF_RETRY)
        self.assertEqual(crawl.get_retry_doc_id_filter("case"), METADATA_ONLY)
        self.assertIsNone(crawl.get_retry_doc_id_filter("interpretation"))
        self.assertEqual(crawl.get_save_incomplete_filter("interpretation"), SAVE_INCOMPLETE)
        self.assertEqual(crawl.get_save_incomplete_filter("case"), SAVE_INCOMPLETE)
        self.assertIsNone(crawl.get_save_incomplete_filter("judgment"))
        self.assertIsNone(crawl.get_save_incomplete_filter("law"))


class EffectiveDatesTest(unittest.TestCase):
    class Coll:
        def __init__(self, docs):
            self.docs = docs
            self.query = None

        def find(self, query, projection):
            self.query = query
            outer = self

            class Cursor(list):
                def max_time_ms(self, ms):
                    return self
            return Cursor(outer.docs)

    def test_active_wins_inactive_fallback_and_incomplete_forces_recrawl(self):
        docs = [
            {"metadata": {"source_url": "a", "effective": "20230101", "is_active": False}},
            {"metadata": {"source_url": "a", "effective": "20240101", "is_active": True}},
            {"metadata": {"source_url": "b", "effective": "20220202", "is_active": False}},
            {"metadata": {"source_url": "c", "effective": "20210101", "is_active": True, "save_incomplete": True}},
            {"metadata": {"effective": "x"}},
        ]
        coll = self.Coll(docs)
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value={"law": coll}):
            result = crawl.get_crawled_effective_dates("law", url_list=["a", "b", "c"])
        self.assertEqual(result, {"a": "20240101", "b": "20220202", "c": None})
        self.assertEqual(coll.query, {"metadata.source_url": {"$in": ["a", "b", "c"]}})

    def test_lookup_failure_is_runtime_error(self):
        with patch("scripts.core.database.mongo_client.get_mongo_db", side_effect=ValueError("down")):
            with self.assertRaisesRegex(RuntimeError, "MongoDB effective 조회 실패"):
                crawl.get_crawled_effective_dates("law")


class ResultShapeTest(unittest.TestCase):
    def test_full_run_result_keys(self):
        async def fetch_urls(start_url, max_pages_arg):
            return [{"url": "https://x/d?precSeq=1"}, {"url": "https://x/d?precSeq=2"}]

        async def scrape(url, **kwargs):
            return {"status": "success", "doc_id": "1"}  # action 없음 → insert 로 센다

        result = run_scraper("case", fetch_urls, scrape)
        self.assertEqual(result, {
            "status": "success", "total_urls": 2, "total_success": 2, "total_no_change": 0,
            "total_failed": 0, "skipped": 0, "failed_targets": [], "failure_reasons": {},
        })

    def test_all_documents_failed_is_failed_status_but_not_listing_failure(self):
        async def fetch_urls(start_url, max_pages_arg):
            return [{"url": "https://x/d?precSeq=1"}]

        async def scrape(url, **kwargs):
            return None

        result = run_scraper("case", fetch_urls, scrape)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["total_failed"], 1)
        self.assertEqual(crawl.compute_exit_code({"case": result}), crawl.EXIT_OK)

    def test_listing_failure_results(self):
        async def broken(start_url, max_pages_arg):
            raise ConnectionError("reset")

        async def empty(start_url, max_pages_arg):
            return []

        raised = run_scraper("law", broken, None)
        self.assertEqual(raised, {
            "status": "failed", "error": "목록 수집 실패: reset", "total_urls": 0,
            "total_success": 0, "total_failed": 0, "failed_targets": ["law:listing"],
        })
        none_found = run_scraper("law", empty, None)
        self.assertEqual(none_found, {
            "status": "failed", "error": "No URLs found", "total_urls": 0,
            "total_success": 0, "total_failed": 0, "failed_targets": ["law:listing"],
        })

    def test_unexpected_error_result_keys(self):
        result = crawl.unexpected_error_result("case", RuntimeError("x"), 4, {"기타 오류": 2})
        self.assertEqual(result, {
            "status": "failed", "error": "x", "error_kind": "unexpected", "total_urls": 4,
            "total_success": 0, "total_failed": 1, "failed_targets": ["case:run"],
            "failure_reasons": {"기타 오류": 2},
        })
        self.assertNotIn("failure_reasons", crawl.unexpected_error_result("case", RuntimeError("x")))

    def test_scrape_arguments_and_jsonl_flag(self):
        got = {}

        async def fetch_urls(start_url, max_pages_arg):
            return [{"url": "https://x/d?precSeq=1"}]

        async def scrape(**kwargs):
            got.update(kwargs)
            return {"status": "success", "doc_id": "1"}

        with patch.dict(os.environ, {"SAVE_JSONL": "false"}):
            run_scraper("judgment", fetch_urls, scrape)
        self.assertEqual(got["url"], {"url": "https://x/d?precSeq=1"})
        self.assertEqual(got["dept_code"], "nlrc")
        self.assertTrue(got["save_to_db"])
        self.assertFalse(got["save_jsonl"])
        self.assertTrue(got["output_name"].startswith("direct_"))
        self.assertTrue(got["output_dir"].endswith(os.path.join("data", "output")))


class FailedDocTargetTest(unittest.TestCase):
    def test_target_prefers_doc_id_then_seq_then_url(self):
        self.assertEqual(crawl.failed_doc_target({"url": "u?precSeq=5"}, {"doc_id": "77"}), "doc:77")
        self.assertEqual(crawl.failed_doc_target({"url": "u?precSeq=5"}, {"status": "failed"}), "doc:5")
        self.assertEqual(crawl.failed_doc_target("u?jgmtSn=8", None), "doc:8")
        self.assertEqual(crawl.failed_doc_target({"doc_seq": "123"}, None), "url:123")
        self.assertEqual(crawl.failed_doc_target({"url": "https://x/nodigits"}, None), "url:https://x/nodigits")
        self.assertEqual(crawl.failed_doc_target({}, None), "url:None")

    def test_failed_targets_in_result_are_prefixed_and_ordered_after_listing_failures(self):
        async def fetch_urls(start_url, max_pages_arg):
            from scripts.core.run_report import record_failure
            record_failure("list-page:2", "x")
            return [{"url": "https://x/d?precSeq=1"}, {"url": "https://x/d?precSeq=2"}, {"url": "https://x/nodigits"}]

        async def scrape(url, **kwargs):
            return {"status": "failed", "doc_id": "D" + url["url"][-1]}

        result = run_scraper("case", fetch_urls, scrape)
        self.assertEqual(result["failed_targets"], ["case:list-page:2", "case:doc:D1", "case:doc:D2", "case:doc:Ds"])
        self.assertEqual(result["total_failed"], 4)


class StartOrderTest(unittest.TestCase):
    def test_default_interval_is_three_seconds(self):
        self.assertEqual(crawl.SCRAPER_START_INTERVAL_SECONDS, 3.0)

    def test_scrapers_start_in_given_order_with_staggered_delay(self):
        seen = []

        def fake_thread_run(args):
            seen.append(args)
            return {"status": "success", "total_urls": 1}

        with patch.object(crawl, "_run_scraper_in_thread", fake_thread_run), \
             patch.object(crawl, "SCRAPER_START_INTERVAL_SECONDS", 2.0):
            results = asyncio.run(crawl.run_multiple_scrapers(["case", "law", "judgment"], 3, 5, "update"))
        self.assertEqual(list(results), ["case", "law", "judgment"])
        self.assertEqual(sorted(seen), sorted([
            ("case", 3, 5, "update", 0.0), ("law", 3, 5, "update", 2.0), ("judgment", 3, 5, "update", 4.0),
        ]))

    def test_thread_exception_becomes_unexpected_error_result(self):
        def fake_thread_run(args):
            raise RuntimeError("thread died")

        with patch.object(crawl, "_run_scraper_in_thread", fake_thread_run):
            results = asyncio.run(crawl.run_multiple_scrapers(["case", "law"], 3, None, "full"))
        for name in ("case", "law"):
            self.assertEqual(results[name]["error_kind"], "unexpected")
            self.assertEqual(results[name]["failed_targets"], [f"{name}:run"])

    def test_sleep_only_when_delay_given(self):
        with patch.object(crawl.time, "sleep") as sleep, \
             patch.object(crawl, "run_single_scraper", lambda *a: asyncio.sleep(0, {"status": "success"})):
            crawl._run_scraper_in_thread(("case", 1, None, "full", 0))
            sleep.assert_not_called()
            crawl._run_scraper_in_thread(("case", 1, None, "full", 6.0))
            sleep.assert_called_once_with(6.0)


class ResultJsonTest(unittest.TestCase):
    def test_write_result_json_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            crawl.write_result_json(path, {"case": {"status": "success", "total_urls": 1}}, 0)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"exit_code": 0, "scrapers": {"case": {"status": "success", "total_urls": 1}}})
        crawl.write_result_json(None, {}, 0)  # 경로 없으면 아무것도 쓰지 않는다


if __name__ == "__main__":
    unittest.main()
