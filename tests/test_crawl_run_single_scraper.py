"""crawl.run_single_scraper 결과 모양: 정상 / 목록 실패 / 예상 밖 예외. 가짜 스크래퍼 모듈만 쓴다."""
import asyncio
import types
import unittest
from unittest.mock import patch

import crawl


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

    def test_unexpected_exception_currently_looks_like_listing_failure(self):
        async def fetch_urls(start_url, max_pages_arg):
            return [{"url": "https://x/prec?precSeq=1"}]

        async def scrape(url, **kwargs):
            return {"status": "success", "doc_id": "1"}

        def broken_filter(*args, **kwargs):
            raise RuntimeError("mongo auth failed")

        with patch.object(crawl, "get_all_crawled_urls", broken_filter):
            result = run(fetch_urls, scrape, mode="update")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["total_urls"], 0)
        self.assertEqual(result["failed_targets"], ["case:run"])
        self.assertEqual(crawl.compute_exit_code({"case": result}), crawl.EXIT_LISTING_FAILURE)


if __name__ == "__main__":
    unittest.main()
