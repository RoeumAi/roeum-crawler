"""crawl.run_single_scraper 결과 모양: 정상 / 목록 실패 / 예상 밖 예외. 가짜 스크래퍼 모듈만 쓴다."""
import asyncio
import types
import unittest
from unittest.mock import patch

import crawl
from scripts.report import batch_result


def fake_modules(fetch_urls, scrape_and_save):
    modules = {
        "scripts.case.logic.list_scraper": types.SimpleNamespace(fetch_urls=fetch_urls),
        "scripts.case.logic.scraper": types.SimpleNamespace(scrape_and_save=scrape_and_save),
    }
    return lambda name: modules[name]


def run(fetch_urls, scrape_and_save, mode="full", cwd=None):
    with patch.object(crawl, "import_module", fake_modules(fetch_urls, scrape_and_save)), \
         patch("os.getcwd", return_value=cwd or "/tmp"):
        return asyncio.run(crawl.run_single_scraper("case", 2, None, mode))


class RunSingleScraperResultTest(unittest.TestCase):
    def test_normal_run_counts_success_no_change_and_failed(self):
        async def fetch_urls(start_url, max_pages_arg):
            return [{"url": f"https://x/prec?precSeq={i}"} for i in (1, 2, 3)]

        async def scrape(url, **kwargs):
            seq = url["url"][-1]
            if seq == "1":
                return {"status": "success", "doc_id": "1", "action": "insert"}
            if seq == "2":
                return {"status": "success", "doc_id": "2", "action": "no_change"}
            return {"status": "failed", "doc_id": "3"}

        result = run(fetch_urls, scrape)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_urls"], 3)
        self.assertEqual(result["total_success"], 1)
        self.assertEqual(result["total_no_change"], 1)
        self.assertEqual(result["total_failed"], 1)
        self.assertEqual(result["failed_targets"], ["case:doc:3"])

    def test_empty_listing_is_a_listing_failure(self):
        async def fetch_urls(start_url, max_pages_arg):
            return []

        result = run(fetch_urls, None)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["total_urls"], 0)
        self.assertEqual(result["failed_targets"], ["case:listing"])
        self.assertEqual(crawl.compute_exit_code({"case": result}), crawl.EXIT_LISTING_FAILURE)

    def test_unexpected_exception_is_not_a_listing_failure(self):
        async def fetch_urls(start_url, max_pages_arg):
            return [{"url": "https://x/prec?precSeq=1"}]

        async def scrape(url, **kwargs):
            return {"status": "success", "doc_id": "1"}

        def broken_filter(*args, **kwargs):
            raise RuntimeError("mongo auth failed")

        with patch.object(crawl, "get_all_crawled_urls", broken_filter):
            result = run(fetch_urls, scrape, mode="update")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_kind"], "unexpected")
        self.assertEqual(result["total_urls"], 1)  # 찾은 목록 수는 덮어쓰지 않는다
        self.assertEqual(result["total_failed"], 1)
        self.assertEqual(result["failed_targets"], ["case:run"])
        # 15분 뒤 다시 해도 같은 오류 → 목록 실패 재시도(종료코드 2) 경로로 보내지 않는다.
        self.assertEqual(crawl.compute_exit_code({"case": result}), crawl.EXIT_OK)
        self.assertEqual(batch_result.retry_scrapers({"scrapers": {"case": result}}), [])

    def test_exception_while_listing_is_still_a_listing_failure(self):
        async def fetch_urls(start_url, max_pages_arg):
            raise ConnectionError("law.go.kr reset")

        result = run(fetch_urls, None)
        self.assertEqual(result["total_urls"], 0)
        self.assertNotIn("error_kind", result)
        self.assertEqual(result["failed_targets"], ["case:listing"])
        self.assertEqual(crawl.compute_exit_code({"case": result}), crawl.EXIT_LISTING_FAILURE)
        self.assertEqual(batch_result.retry_scrapers({"scrapers": {"case": result}}), ["case"])

    def test_unexpected_error_is_reported_as_one_failure_in_batch_result(self):
        result = crawl.unexpected_error_result("case", RuntimeError("x"))
        payload, exit_code = batch_result.build_batch_result(
            {"case": result}, missing_attempt=False, last_crawl_exit_code=0,
            refresh_exit_code=0, attempts=1, duration_minutes=1)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(payload["failedTargets"], ["case:run"])
        self.assertEqual(exit_code, 0)

    def test_document_failure_reasons_feed_run_report_counts(self):
        async def fetch_urls(start_url, max_pages_arg):
            return [{"url": f"https://x/prec?precSeq={i}"} for i in (1, 2, 3, 4)]

        async def scrape(url, **kwargs):
            seq = url["url"][-1]
            if seq == "1":
                return {"status": "failed", "error": "ReadTimeout: Read timed out"}
            if seq == "2":
                raise RuntimeError("chat_generation 연결 실패")
            if seq == "3":
                return {"status": "failed", "error": "MongoDB 저장 실패"}  # 분류 불가 → 요약이 기타로 센다
            return {"status": "success", "doc_id": "4"}

        result = run(fetch_urls, scrape)
        self.assertEqual(result["total_failed"], 3)
        self.assertEqual(result["failure_reasons"], {"응답 시간 초과": 1, "문서 추출 서버(chat_generation) 연결 실패": 1})

if __name__ == "__main__":
    unittest.main()
