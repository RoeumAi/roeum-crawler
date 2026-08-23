import unittest

from scripts.notify.daily_summary import build_message, parse_scraper_results


SUCCESS_LOG = """
✅ 법령 (law)
   - 발견 URL: 165개
   - 성공: 0개
   - 변경없음: 0개
   - 실패: 0개
   - 건너뜀: 165개 (이미 크롤링됨)

✅ 판례 (case)
   - 발견 URL: 30,000개
   - 성공: 3개
   - 변경없음: 0개
   - 실패: 0개
   - 건너뜀: 29,997개 (이미 크롤링됨)

================================================================================
📈 전체 통계
================================================================================
"""

# law.go.kr 404 로 목록조차 못 모은 밤 (2026-08-22 재현)
LISTING_FAILURE_LOG = """
❌ 법령 (law)
   - 발견 URL: 0개
   - 성공: 0개
   - 변경없음: 0개
   - 실패: 0개

✅ 심의결정례 (decision)
   - 발견 URL: 511개
   - 성공: 0개
   - 변경없음: 0개
   - 실패: 0개
   - 건너뜀: 511개 (이미 크롤링됨)

================================================================================
📈 전체 통계
================================================================================
"""


class ParseScraperResultsTest(unittest.TestCase):
    def test_listing_failure_detected_by_zero_urls(self):
        results = parse_scraper_results(LISTING_FAILURE_LOG)
        self.assertEqual(results["law"]["status"], "❌")
        self.assertEqual(results["law"]["total"], 0)
        self.assertEqual(results["decision"]["status"], "✅")
        self.assertEqual(results["decision"]["skipped"], 511)


class BuildMessageTest(unittest.TestCase):
    def test_success_message_has_no_alarm_footer(self):
        msg = build_message(SUCCESS_LOG, duration_min=19, attempts=1, date_str="2026-08-23 00:00")
        self.assertIn("일일 크롤러 업데이트", msg)
        self.assertIn("✅ 판례", msg)
        self.assertNotIn("외부 수집원", msg)
        self.assertNotIn("재시도", msg)

    def test_listing_failure_uses_external_source_wording_not_generic_error(self):
        msg = build_message(LISTING_FAILURE_LOG, duration_min=32, attempts=3, date_str="2026-08-22 00:00")
        # 원래 "오류 발생" 대신 외부 수집원 문구로 바뀌어야 한다
        self.assertNotIn("오류 발생", msg)
        self.assertIn("수집원", msg)
        self.assertIn("법령", msg)

    def test_listing_failure_reports_retry_count_and_no_data_loss(self):
        msg = build_message(LISTING_FAILURE_LOG, duration_min=32, attempts=3, date_str="2026-08-22 00:00")
        self.assertIn("2회 재시도", msg)          # attempts=3 → 재시도 2회
        self.assertIn("데이터 유실", msg)          # 안심 문구

    def test_single_attempt_success_omits_retry_line(self):
        msg = build_message(SUCCESS_LOG, duration_min=19, attempts=1, date_str="2026-08-23 00:00")
        self.assertNotIn("재시도", msg)


if __name__ == "__main__":
    unittest.main()
