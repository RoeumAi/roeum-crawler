import unittest

from scripts.report.batch_result import (
    EXIT_FATAL,
    EXIT_LISTING_FAILURE,
    EXIT_OK,
    MAX_FAILED_TARGETS,
    build_batch_result,
    merge_attempts,
    retry_scrapers,
)


def build(scrapers, **overrides):
    kwargs = dict(missing_attempt=False, last_crawl_exit_code=0, refresh_exit_code=0,
                  attempts=1, duration_minutes=21)
    kwargs.update(overrides)
    return build_batch_result(scrapers, **kwargs)


class BuildBatchResultTest(unittest.TestCase):
    def test_all_success_reports_zero_failed(self):
        result, code = build({
            "law": {"status": "success", "total_urls": 161, "total_success": 2, "total_no_change": 1},
            "case": {"status": "success", "total_urls": 16000, "total_success": 0, "skipped": 16000},
        })

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(result["processed"], 3)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["failedTargets"], [])
        self.assertIn("처리 3", result["summary"])

    def test_partial_document_and_listing_page_failures_are_counted(self):
        # 예전에는 이런 날도 ✅·종료코드 0으로만 보였다.
        result, code = build({
            "legislation_expc": {"status": "success", "total_urls": 137, "total_success": 0,
                                 "total_failed": 1, "failed_targets": ["legislation_expc:list-page:3"]},
            "interpretation": {"status": "success", "total_urls": 7384, "total_success": 1,
                               "total_failed": 1, "failed_targets": ["interpretation:doc:334455"]},
        })

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(result["failed"], 2)
        self.assertEqual(result["failedTargets"],
                         ["legislation_expc:list-page:3", "interpretation:doc:334455"])
        self.assertIn("법제처해석례 1", result["summary"])
        self.assertIn("해석례 1", result["summary"])

    def test_listing_failure_after_retries_is_failed_exit(self):
        result, code = build({
            "law": {"status": "failed", "error": "RemoteDisconnected", "total_urls": 0,
                    "failed_targets": ["law:listing"]},
        }, last_crawl_exit_code=2, attempts=3)

        self.assertEqual(code, EXIT_LISTING_FAILURE)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["failedTargets"], ["law:listing"])

    def test_refresh_failure_fails_the_job(self):
        result, code = build({"law": {"status": "success", "total_urls": 161}}, refresh_exit_code=1)

        self.assertEqual(code, EXIT_FATAL)
        self.assertIn("refresh:current-status", result["failedTargets"])
        self.assertIn("시행 버전 재계산 실패", result["summary"])

    def test_missing_result_file_means_crawler_crashed(self):
        result, code = build({}, missing_attempt=True, last_crawl_exit_code=1)

        self.assertEqual(code, EXIT_FATAL)
        self.assertEqual(result["failedTargets"], ["crawl:run"])

    def test_failed_targets_are_capped(self):
        targets = [f"case:doc:{i}" for i in range(80)]
        result, _ = build({"case": {"status": "success", "total_urls": 100, "total_success": 20,
                                    "total_failed": 80, "failed_targets": targets}})

        self.assertEqual(result["failed"], 80)
        self.assertEqual(len(result["failedTargets"]), MAX_FAILED_TARGETS)


class AttemptsTest(unittest.TestCase):
    def test_later_attempt_overrides_only_retried_scrapers(self):
        first = {"exit_code": 2, "scrapers": {
            "case": {"status": "success", "total_urls": 16000, "total_success": 1},
            "law": {"status": "failed", "total_urls": 0},
        }}
        second = {"exit_code": 0, "scrapers": {
            "law": {"status": "success", "total_urls": 161, "total_success": 2},
        }}

        merged, missing = merge_attempts([first, second])

        self.assertFalse(missing)
        self.assertEqual(merged["case"]["total_success"], 1)
        self.assertEqual(merged["law"]["total_success"], 2)

    def test_missing_attempt_is_flagged(self):
        _, missing = merge_attempts([None])
        self.assertTrue(missing)

    def test_retry_scrapers_picks_listing_failures_only(self):
        attempt = {"scrapers": {
            "law": {"status": "failed", "total_urls": 0},
            "case": {"status": "success", "total_urls": 10},
            "judgment": {"status": "failed", "total_urls": 417, "total_failed": 3},
        }}

        self.assertEqual(retry_scrapers(attempt), ["law"])
        self.assertEqual(retry_scrapers(None), [])


if __name__ == "__main__":
    unittest.main()
