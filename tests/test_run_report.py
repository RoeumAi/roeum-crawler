import asyncio
import threading
import unittest
from unittest.mock import patch

from scripts.core.run_report import call_with_retry, record_failure, start_run_report


class RunReportTest(unittest.TestCase):
    def test_failures_are_prefixed_with_scraper(self):
        async def run():
            report = start_run_report("case")
            record_failure("list-page:17", TimeoutError("Read timed out"))
            return report

        report = asyncio.run(run())

        self.assertEqual(report.failed_targets, ["case:list-page:17"])

    def test_record_without_report_is_ignored(self):
        def run():
            record_failure("list-page:1", "boom")  # 단독 실행(목록 스크래퍼 직접 실행)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()

    def test_reports_are_isolated_per_scraper_thread(self):
        reports = {}

        def run(name):
            async def scrape():
                report = start_run_report(name)
                await asyncio.sleep(0)
                # 같은 컨텍스트에서 만든 스레드 작업에서도 보인다.
                await asyncio.to_thread(record_failure, f"list-page:{name}", "x")
                return report
            reports[name] = asyncio.run(scrape())

        threads = [threading.Thread(target=run, args=(n,)) for n in ("law", "adrule")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(reports["law"].failed_targets, ["law:list-page:law"])
        self.assertEqual(reports["adrule"].failed_targets, ["adrule:list-page:adrule"])


class CallWithRetryTest(unittest.TestCase):
    def test_second_attempt_result_is_returned(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("RemoteDisconnected")
            return "ok"

        with patch("scripts.core.run_report.time.sleep"):
            self.assertEqual(call_with_retry(flaky), "ok")
        self.assertEqual(len(calls), 2)

    def test_last_error_is_raised(self):
        def broken():
            raise ConnectionError("down")

        with patch("scripts.core.run_report.time.sleep"):
            with self.assertRaises(ConnectionError):
                call_with_retry(broken, attempts=3)


if __name__ == "__main__":
    unittest.main()
