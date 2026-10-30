"""상세 페이지·목록 페이지 요청 재시도: 일시 오류만 다시 시도하고, 시간·총량 상한을 지키며,
소진되면 예전과 같은 실패 결과를 낸다. 실제 대기·네트워크 없음(가짜 sleep·clock)."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import requests
from curl_cffi.requests import exceptions as curl_exceptions

from scripts.core import run_report
from scripts.core.run_report import (
    call_with_retry, call_with_retry_async, is_transient_error, retry_detail_fetch,
    retry_detail_fetch_async, start_run_report,
)


class FakeClock:
    """sleep 만큼, 그리고 시도마다 attempt_seconds 만큼 시간이 흐르는 가짜 시계."""

    def __init__(self, attempt_seconds=0.0):
        self.now = 0.0
        self.attempt_seconds = attempt_seconds
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    async def asleep(self, seconds):
        self.sleep(seconds)

    def attempt(self):
        self.now += self.attempt_seconds


def http_error(status, cls=requests.HTTPError):
    response = Mock(status_code=status)
    return cls(f"{status} error", response=response)


class IsTransientTest(unittest.TestCase):
    def test_transient(self):
        for exc in (
            requests.ConnectionError("reset"), requests.Timeout("slow"), requests.exceptions.ReadTimeout("slow"),
            curl_exceptions.ConnectionError("reset"), curl_exceptions.Timeout("slow"),
            ConnectionResetError("x"), TimeoutError("x"), http_error(500), http_error(503),
            RuntimeError("Page.goto: net::ERR_CONNECTION_RESET at https://x"),
            RuntimeError("net::ERR_TIMED_OUT"),
        ):
            self.assertTrue(is_transient_error(exc), repr(exc))

    def test_playwright_timeout_is_transient(self):
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        self.assertTrue(is_transient_error(PlaywrightTimeout("Timeout 60000ms exceeded")))

    def test_permanent(self):
        for exc in (
            http_error(404), http_error(403), http_error(400), ValueError("파싱 실패"), KeyError("x"),
            AttributeError("none"), RuntimeError("net::ERR_ABORTED"), requests.exceptions.SSLError("cert"),
            http_error(404, cls=curl_exceptions.HTTPError),
        ):
            self.assertFalse(is_transient_error(exc), repr(exc))


class SyncRetryTest(unittest.TestCase):
    def run_fn(self, outcomes, **kwargs):
        clock = FakeClock(kwargs.pop("attempt_seconds", 0.0))
        calls = []

        def fn():
            calls.append(1)
            clock.attempt()
            outcome = outcomes[min(len(calls), len(outcomes)) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        try:
            result = call_with_retry(fn, sleep=clock.sleep, clock=clock, **kwargs)
        except Exception as exc:
            result = exc
        return result, len(calls), clock

    def test_transient_error_then_success(self):
        result, calls, clock = self.run_fn([requests.ConnectionError("x"), "ok"], retry_if=is_transient_error)
        self.assertEqual((result, calls, clock.sleeps), ("ok", 2, [5.0]))

    def test_permanent_4xx_is_not_retried(self):
        error = http_error(404)
        result, calls, clock = self.run_fn([error], retry_if=is_transient_error)
        self.assertIs(result, error)
        self.assertEqual((calls, clock.sleeps), (1, []))

    def test_parse_error_is_not_retried(self):
        error = ValueError("bad html")
        result, calls, clock = self.run_fn([error], retry_if=is_transient_error)
        self.assertIs(result, error)
        self.assertEqual(calls, 1)

    def test_exhaustion_raises_last_error_after_three_attempts(self):
        errors = [http_error(503), http_error(502), http_error(500)]
        result, calls, clock = self.run_fn(errors, retry_if=is_transient_error)
        self.assertIs(result, errors[-1])
        self.assertEqual((calls, clock.sleeps), (3, [5.0, 10.0]))

    def test_without_filter_every_exception_is_retried_like_list_pages(self):
        result, calls, clock = self.run_fn([http_error(404), "ok"])
        self.assertEqual((result, calls), ("ok", 2))

    def test_per_call_time_cap_stops_slow_failures(self):
        # 시도마다 60초 걸리면: 60 + 5 < 90 이라 한 번 더, 125 + 10 >= 90 이라 멈춘다.
        result, calls, clock = self.run_fn([TimeoutError("slow")] * 3, retry_if=is_transient_error,
                                           max_total_seconds=90.0, attempt_seconds=60.0)
        self.assertIsInstance(result, TimeoutError)
        self.assertEqual((calls, clock.sleeps), (2, [5.0]))

    def test_on_retry_hook_gets_attempt_number(self):
        seen = []
        self.run_fn([TimeoutError("x"), "ok"], retry_if=is_transient_error, on_retry=lambda n, e: seen.append(n))
        self.assertEqual(seen, [1])

    def test_default_sleep_is_resolved_at_call_time(self):
        with patch("scripts.core.run_report.time.sleep") as sleep:
            call_with_retry(_flaky_once())
        sleep.assert_called_once_with(5.0)


def _flaky_once():
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] == 1:
            raise ConnectionError("x")
        return "ok"
    return fn


class BudgetTest(unittest.TestCase):
    def test_run_budget_stops_retrying_when_used_up(self):
        async def scenario():
            report = start_run_report("case")
            report.retry_seconds_used = run_report.DETAIL_RETRY_BUDGET_SECONDS - 3  # 대기 5초가 남은 양을 넘는다
            clock = FakeClock()
            calls = []

            def fn():
                calls.append(1)
                raise TimeoutError("slow")

            with self.assertRaises(TimeoutError):
                call_with_retry(fn, retry_if=is_transient_error, budgeted=True, sleep=clock.sleep, clock=clock)
            return len(calls), clock.sleeps, report

        calls, sleeps, report = asyncio.run(scenario())
        self.assertEqual((calls, sleeps), (1, []))

    def test_waits_and_retry_attempts_are_charged_to_the_run(self):
        async def scenario():
            report = start_run_report("case")
            clock = FakeClock(attempt_seconds=2.0)
            calls = []

            def fn():
                calls.append(1)
                clock.attempt()
                if len(calls) < 3:
                    raise TimeoutError("slow")
                return "ok"

            call_with_retry(fn, retry_if=is_transient_error, budgeted=True, sleep=clock.sleep, clock=clock)
            return report.retry_seconds_used

        # 대기 5 + 10, 재시도 요청 2번 × 2초 (첫 시도는 세지 않는다)
        self.assertEqual(asyncio.run(scenario()), 19.0)

    def test_without_run_report_there_is_no_budget_limit(self):
        def run():
            clock = FakeClock()
            return call_with_retry(_flaky_once(), retry_if=is_transient_error, budgeted=True, sleep=clock.sleep, clock=clock)
        import threading
        out = []
        t = threading.Thread(target=lambda: out.append(run()))
        t.start(); t.join()
        self.assertEqual(out, ["ok"])


class AsyncRetryTest(unittest.TestCase):
    def test_transient_then_success_and_exhaustion(self):
        async def scenario():
            clock = FakeClock()
            state = {"n": 0}

            async def flaky():
                state["n"] += 1
                if state["n"] == 1:
                    raise requests.ConnectionError("x")
                return "ok"

            ok = await call_with_retry_async(flaky, retry_if=is_transient_error, sleep=clock.asleep, clock=clock)
            errors = [http_error(500), http_error(500), http_error(502)]
            seq = iter(errors)

            async def broken():
                raise next(seq)

            with self.assertRaises(requests.HTTPError) as caught:
                await call_with_retry_async(broken, retry_if=is_transient_error, sleep=clock.asleep, clock=clock)
            return ok, caught.exception is errors[-1], clock.sleeps

        self.assertEqual(asyncio.run(scenario()), ("ok", True, [5.0, 5.0, 10.0]))

    def test_permanent_error_not_retried(self):
        async def scenario():
            calls = []

            async def fn():
                calls.append(1)
                raise http_error(404)

            with self.assertRaises(requests.HTTPError):
                await retry_detail_fetch_async(fn)
            return len(calls)

        with patch("scripts.core.run_report.asyncio.sleep", new=AsyncMock()) as sleep:
            self.assertEqual(asyncio.run(scenario()), 1)
        sleep.assert_not_called()

    def test_retry_detail_fetch_defaults(self):
        with patch("scripts.core.run_report.time.sleep") as sleep:
            self.assertEqual(retry_detail_fetch(_flaky_once()), "ok")
        sleep.assert_called_once_with(5.0)


# ---- 상세 수집 지점별 ----------------------------------------------------------------------------

def ok_response(html):
    resp = Mock()
    resp.text = html
    resp.raise_for_status.return_value = None
    return resp


def bad_response(status):
    resp = Mock(status_code=status)
    resp.raise_for_status.side_effect = requests.HTTPError(f"{status}", response=resp)
    return resp


class DetailSitesTest(unittest.TestCase):
    def setUp(self):
        self.sleep = patch("scripts.core.run_report.asyncio.sleep", new=AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def scrape_section(self, effects):
        from scripts.constitutional_decc.logic import scraper
        with patch("requests.get", side_effect=effects) as get:
            result = asyncio.run(scraper.scrape_and_save(
                "https://www.law.go.kr/LSW/x.do?detcSeq=1", "", "t", save_to_db=False, save_jsonl=False))
        return result, get.call_count

    def test_section_scraper_retries_transient_error(self):
        html = '<div id="contentBody"><h2>제목</h2><div class="subtit1">[2019헌바1, 2022. 1. 1.]</div><div id="conScroll"><h4>【이유】</h4><p>본문</p></div></div>'
        result, calls = self.scrape_section([requests.ConnectionError("reset"), ok_response(html)])
        self.assertEqual(result["status"], "success")
        self.assertEqual(calls, 2)

    def test_section_scraper_4xx_fails_once_with_same_result_shape(self):
        result, calls = self.scrape_section([bad_response(404)])
        self.assertEqual(calls, 1)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["doc_id"])
        self.assertTrue(result["error"].startswith("HTTPError: "))

    def test_section_scraper_exhaustion_reports_last_error(self):
        result, calls = self.scrape_section([bad_response(503)] * 3)
        self.assertEqual(calls, 3)
        self.assertEqual(result, {"doc_id": None, "status": "failed", "error": "HTTPError: 503"})

    def test_case_scraper_retries_then_fails_like_before(self):
        from scripts.case.logic import scraper
        with patch("requests.get", side_effect=[requests.Timeout("slow")] * 3) as get:
            result = asyncio.run(scraper.scrape_and_save("https://www.law.go.kr/x?precSeq=1", "", "t", save_to_db=False, save_jsonl=False))
        self.assertEqual(get.call_count, 3)
        self.assertEqual(result["status"], "failed")

    def test_nlrc_scrapers_use_retry_on_transient_fetch_error(self):
        from scripts.judgment.logic import scraper as judgment
        from scripts.mediation_case.logic import scraper as mediation
        for module, url in ((judgment, "https://nlrc.go.kr/d?jgmtSn=1"), (mediation, "https://nlrc.go.kr/d?jgmtSn=1&jgmtDcsnSeCd=66")):
            with patch.object(module, "_fetch_detail_html", side_effect=[curl_exceptions.ConnectionError("x"), RuntimeError("stop")]) as fetch:
                result = asyncio.run(module.scrape_and_save(url, "", "t", save_to_db=False, save_jsonl=False))
            self.assertEqual(fetch.call_count, 2)  # 일시 오류는 다시, 그 다음 RuntimeError 는 바로 실패
            self.assertEqual(result, {"doc_id": None, "status": "failed", "error": "RuntimeError: stop"})
            with patch.object(module, "_fetch_detail_html", side_effect=ValueError("parse")) as fetch:
                asyncio.run(module.scrape_and_save(url, "", "t", save_to_db=False, save_jsonl=False))
            self.assertEqual(fetch.call_count, 1)

    def test_lawgokr_page_goto_retries_network_error_only(self):
        from scripts.core.lawgokr_page import load_rendered_page
        logger = Mock()

        class Page:
            def __init__(self, errors):
                self.errors = list(errors)
                self.gotos = 0

            async def goto(self, url, **kwargs):
                self.gotos += 1
                if self.errors:
                    raise self.errors.pop(0)

            async def wait_for_selector(self, *a, **k):
                raise RuntimeError("no mask")

            async def wait_for_timeout(self, ms):
                pass

            async def content(self):
                return "<html/>"

        flaky = Page([RuntimeError("Page.goto: net::ERR_CONNECTION_RESET")])
        html = asyncio.run(load_rendered_page(flaky, "https://x", logger, goto_timeout=1, mask_visible_timeout=1, mask_hidden_timeout=1))
        self.assertEqual((html, flaky.gotos), ("<html/>", 2))

        broken = Page([RuntimeError("Page.goto: invalid url")])
        with self.assertRaises(RuntimeError):
            asyncio.run(load_rendered_page(broken, "https://x", logger, goto_timeout=1, mask_visible_timeout=1, mask_hidden_timeout=1))
        self.assertEqual(broken.gotos, 1)


class ListScraperRetryTest(unittest.TestCase):
    def test_case_and_constitutional_use_shared_helper(self):
        from scripts.case.logic import list_scraper as case_list
        from scripts.constitutional_decc.logic import list_scraper as const_list
        with patch("scripts.core.run_report.time.sleep") as sleep, \
             patch.object(case_list, "_post_list_page", side_effect=[ConnectionError("x"), "page"]), \
             patch.object(const_list, "_post_list_page", side_effect=[ConnectionError("x"), ConnectionError("y"), "page"]):
            self.assertEqual(case_list._post_list_page_with_retry("1492000", 3), "page")
            self.assertEqual(const_list._post_list_page_with_retry(3, Mock()), "page")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5.0, 5.0, 10.0])

    def test_first_page_of_expc_and_admin_decc_is_retried(self):
        from scripts.legislation_expc.logic import list_scraper as expc
        from scripts.admin_decc.logic import list_scraper as admin
        empty_soup = Mock()
        empty_soup.select_one.return_value = None
        empty_soup.select.return_value = []
        with patch("scripts.core.run_report.time.sleep"), \
             patch.object(expc, "_post_list_page", side_effect=[ConnectionError("x"), empty_soup]) as post, \
             patch.object(admin, "_fetch_page", side_effect=[ConnectionError("x"), ([], 0)]) as fetch:
            asyncio.run(expc.fetch_urls("", None))
            asyncio.run(admin.fetch_urls("", None))
        self.assertEqual((post.call_count, fetch.call_count), (2, 2))


if __name__ == "__main__":
    unittest.main()
