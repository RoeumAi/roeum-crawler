import unittest

from crawl import EXIT_OK, EXIT_LISTING_FAILURE, compute_exit_code


class ComputeExitCodeTest(unittest.TestCase):
    def test_all_success_returns_ok(self):
        results = {
            "law": {"status": "success", "total_urls": 165},
            "case": {"status": "success", "total_urls": 30000},
        }
        self.assertEqual(compute_exit_code(results), EXIT_OK)

    def test_listing_failure_zero_urls_is_retryable(self):
        # law.go.kr 404 처럼 URL 목록조차 못 모은 경우 → 재시도 대상
        results = {
            "law": {"status": "failed", "error": "404", "total_urls": 0},
            "mediation_case": {"status": "success", "total_urls": 78, "skipped": 78},
        }
        self.assertEqual(compute_exit_code(results), EXIT_LISTING_FAILURE)

    def test_failed_status_without_total_urls_key_is_retryable(self):
        results = {"law": {"status": "failed", "error": "boom"}}
        self.assertEqual(compute_exit_code(results), EXIT_LISTING_FAILURE)

    def test_doc_level_failures_with_urls_found_are_not_retryable(self):
        # 목록은 정상 수집됐고 개별 문서만 일부 실패 → 전체 재시도 대상 아님
        results = {
            "law": {"status": "failed", "total_urls": 165, "total_success": 100, "total_failed": 65},
        }
        self.assertEqual(compute_exit_code(results), EXIT_OK)

    def test_non_dict_values_are_ignored(self):
        results = {"law": {"status": "success", "total_urls": 1}, "case": ValueError("x")}
        self.assertEqual(compute_exit_code(results), EXIT_OK)


if __name__ == "__main__":
    unittest.main()
