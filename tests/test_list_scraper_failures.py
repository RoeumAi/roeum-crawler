"""목록 수집: 페이지 하나 실패는 기록하고 계속, 페이지 수를 못 읽으면 드러낸다(nlrc 3종·해석례)."""
import unittest
from unittest.mock import patch

from scripts.core.run_report import start_run_report
from scripts.interpretation.logic import list_scraper as interpretation_list
from scripts.utils import nlrc_list


def nlrc_page(ids, pages=None):
    pager = "".join(f'<a data-pageindex="{p}">{p}</a>' for p in range(1, (pages or 0) + 1))
    rows = "".join(f'<tr><td><a data-event="click-detail" data-jgmt-sn="{i}">사건 {i}</a></td><td>2026-10-01</td></tr>'
                   for i in ids)
    return f'<div class="bbs_pagerA">{pager}</div><table>{rows}</table>'


class Logger:
    def info(self, *a): pass
    def error(self, *a): pass


async def fetch(pages: dict, **kw):
    def fetch_page(n):
        value = pages[n]
        if isinstance(value, Exception):
            raise value
        return value
    return await nlrc_list.fetch_nlrc_list(code="65", detail_base_url="https://x/detail.do", referer="https://x",
                                           name_prefix="judgment", logger=Logger(), fetch_page=fetch_page,
                                           retry_wait_seconds=0, **kw)


class NlrcListTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_middle_page_is_recorded_and_others_kept(self):
        report = start_run_report("judgment")
        items = await fetch({1: nlrc_page([1, 2], pages=3), 2: RuntimeError("timeout"), 3: nlrc_page([5])})
        self.assertEqual([i["jgmt_sn"] for i in items], ["1", "2", "5"])
        self.assertEqual(report.failed_targets, ["judgment:list-page:2"])

    async def test_transient_error_is_retried(self):
        report = start_run_report("judgment")
        calls = {"n": 0}

        async def run():
            def flaky(n):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("reset")
                return nlrc_page([1])
            return await nlrc_list.fetch_nlrc_list(code="65", detail_base_url="d", referer="r", name_prefix="j",
                                                   logger=Logger(), fetch_page=flaky, retry_wait_seconds=0)
        items = await run()
        self.assertEqual(len(items), 1)
        self.assertEqual(report.failed_targets, [])

    async def test_first_page_failure_returns_empty_list(self):
        self.assertEqual(await fetch({1: RuntimeError("down")}), [])

    async def test_missing_pager_on_full_page_is_recorded(self):
        report = start_run_report("judgment")
        items = await fetch({1: nlrc_page(range(10))})
        self.assertEqual(len(items), 10)
        self.assertEqual(report.failed_targets, ["judgment:list-pages"])

    async def test_short_single_page_without_pager_is_fine(self):
        report = start_run_report("judgment")
        await fetch({1: nlrc_page([1, 2])})
        self.assertEqual(report.failed_targets, [])

    async def test_decision_keeps_title_and_date(self):
        items = await fetch({1: nlrc_page([7], pages=1)}, with_title_and_date=True)
        self.assertEqual(items[0]["title"], "사건 7")
        self.assertEqual(items[0]["registered_at"], "2026-10-01")


def interpretation_page(seqs, total=None):
    title = f'<div class="tit2">총 {total}건</div>' if total is not None else ""
    links = "".join(f'<a href="cgmExpcInfoP.do?cgmExpcDatSeq={s}&amp;ofiClsCd=1">x</a>' for s in seqs)
    return title + links


class InterpretationListTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_middle_page_is_recorded(self):
        report = start_run_report("interpretation")
        pages = {1: interpretation_page(range(50), total=120), 2: RuntimeError("timeout"),
                 3: interpretation_page([500])}

        def fake(n, dept_code=None):
            if isinstance(pages[n], Exception):
                raise pages[n]
            return pages[n]

        with patch.object(interpretation_list, "_fetch_page", side_effect=fake):
            items = await interpretation_list.fetch_urls("https://x", retry_wait_seconds=0)
        self.assertEqual(len(items), 51)
        self.assertEqual(report.failed_targets, ["interpretation:list-page:2"])

    async def test_unreadable_total_on_full_page_is_recorded(self):
        report = start_run_report("interpretation")
        with patch.object(interpretation_list, "_fetch_page", return_value=interpretation_page(range(50))):
            await interpretation_list.fetch_urls("https://x", retry_wait_seconds=0)
        self.assertEqual(report.failed_targets, ["interpretation:list-pages"])


if __name__ == "__main__":
    unittest.main()
