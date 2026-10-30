"""판례·헌재결정례·법제처해석례 목록: 총 페이지 수를 못 읽어 1페이지만 모으게 되면 목록 실패로 기록한다."""
import asyncio
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from scripts.case.logic import list_scraper as case_list
from scripts.constitutional_decc.logic import list_scraper as const_list
from scripts.core.run_report import start_run_report
from scripts.legislation_expc.logic import list_scraper as expc_list


def page(link_fmt, count, header=""):
    links = "".join(f'<a href="{link_fmt.format(i=i)}">사건 {i}</a>' for i in range(count))
    return BeautifulSoup(f"{header}{links}", "html.parser")


CASES = {
    # 이름: (모듈, 링크 형식, 읽을 수 있는 머리말, 패치 대상)
    "case": (case_list, "precInfoP.do?precSeq={i}", '<div class="tit2">전체 (1/1)</div>', "_post_list_page_with_retry"),
    "constitutional_decc": (const_list, "detcInfoP.do?detcSeq={i}", '<div class="tit2"><strong>3</strong></div>',
                            "_post_list_page_with_retry"),
    "legislation_expc": (expc_list, "expcInfoP.do?expcSeq={i}", '<div class="tit2"><strong>3</strong></div>',
                         "_post_list_page"),
}


def fetch(name, soup):
    module, _, _, target = CASES[name]
    report = start_run_report(name)
    with patch.object(module, target, lambda *args, **kwargs: soup):
        items = asyncio.run(module.fetch_urls("", None))
    return items, report


class TotalPagesUnreadableTest(unittest.TestCase):
    def test_full_first_page_without_total_is_recorded(self):
        for name, (_, fmt, _, _) in CASES.items():
            with self.subTest(scraper=name):
                items, report = fetch(name, page(fmt, 50))
                self.assertEqual(len(items), 50)
                self.assertEqual(report.failed_targets, [f"{name}:list-pages"])

    def test_short_first_page_without_total_is_a_normal_single_page(self):
        for name, (_, fmt, _, _) in CASES.items():
            with self.subTest(scraper=name):
                items, report = fetch(name, page(fmt, 3))
                self.assertEqual(len(items), 3)
                self.assertEqual(report.failed_targets, [])

    def test_readable_total_is_not_recorded(self):
        for name, (_, fmt, header, _) in CASES.items():
            with self.subTest(scraper=name):
                items, report = fetch(name, page(fmt, 3, header))
                self.assertEqual(len(items), 3)
                self.assertEqual(report.failed_targets, [])


if __name__ == "__main__":
    unittest.main()
