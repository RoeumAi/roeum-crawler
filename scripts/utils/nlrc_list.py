"""중앙노동위원회(nlrc.go.kr) 사례 목록 공통 수집.

주요판정사례(65)·조정사건례(66)·심의결정례(67)는 같은 목록 주소에 분류 코드만 바꿔 요청한다.
페이지마다 몇 번 더 시도하고, 그래도 실패한 페이지는 record_failure 로 남긴 뒤 나머지 페이지를 계속 모은다.
첫 페이지를 못 받으면 빈 목록을 돌려준다(crawl.py 가 목록 전체 실패로 보고 → run_daily.sh 가 다시 시도).
"""
import asyncio
import re
from typing import Callable, Optional

from bs4 import BeautifulSoup
from curl_cffi import requests

from scripts.core.run_report import call_with_retry, record_failure

LIST_ENDPOINT = "https://nlrc.go.kr/nlrc/mainCase/mainJudgment/list.do"
RECORD_COUNT = 10
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def post_list_html(code: str, page_index: int, referer: str) -> str:
    data = {
        "jgmtDcsnSeCd": code,
        "dataSeCd": "",
        "pageIndex": str(page_index),
        "recordCountPerPage": str(RECORD_COUNT),
        "likeColumn": "TTL",
        "likeCondition": "",
    }
    headers = {"User-Agent": USER_AGENT, "Referer": referer}
    response = requests.post(LIST_ENDPOINT, data=data, headers=headers, timeout=30, impersonate="chrome110")
    response.raise_for_status()
    return response.text


def parse_total_pages(html: str) -> Optional[int]:
    """페이지 번호 중 가장 큰 값. 페이지 표시가 없으면 None."""
    soup = BeautifulSoup(html, "html.parser")
    pages = [int(v) for el in soup.select(".bbs_pagerA [data-pageindex]")
             if (v := (el.get("data-pageindex") or "").strip()).isdigit()]
    return max(pages) if pages else None


def parse_rows(html: str, code: str, detail_base_url: str, name_prefix: str,
               with_title_and_date: bool = False) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for row in soup.select("a[data-event='click-detail'][data-jgmt-sn]"):
        jgmt_sn = (row.get("data-jgmt-sn") or "").strip()
        if not jgmt_sn:
            continue
        jgmt_dcsn_se_cd = (row.get("data-jgmt-dcsn-se-cd") or code).strip()
        title = row.get_text(strip=True)
        item = {
            "name": re.sub(r'[\\/*?:"<>|]', "", title).strip() if title else f"{name_prefix}_{jgmt_sn}",
            "url": f"{detail_base_url}?jgmtSn={jgmt_sn}&jgmtDcsnSeCd={jgmt_dcsn_se_cd}",
            "jgmt_sn": jgmt_sn,
            "jgmt_dcsn_se_cd": jgmt_dcsn_se_cd,
        }
        if with_title_and_date:
            parent_tr = row.find_parent("tr")
            tds = parent_tr.select("td") if parent_tr else []
            item["title"] = title
            item["registered_at"] = tds[-1].get_text(strip=True) if tds else ""
        items.append(item)
    return items


async def fetch_nlrc_list(
    *,
    code: str,
    detail_base_url: str,
    referer: str,
    name_prefix: str,
    logger,
    max_pages: Optional[int] = None,
    with_title_and_date: bool = False,
    fetch_page: Optional[Callable[[int], str]] = None,
    retry_wait_seconds: float = 3.0,
) -> list[dict]:
    fetch = fetch_page or (lambda page: post_list_html(code, page, referer))

    def get(page: int) -> str:
        return call_with_retry(lambda: fetch(page), attempts=3, wait_seconds=retry_wait_seconds)

    try:
        first_html = await asyncio.to_thread(get, 1)
    except Exception as e:  # noqa: BLE001 - 목록 전체 실패는 빈 목록으로 알린다
        logger.error(f"❌ 목록 첫 페이지 수집 실패: {e}")
        return []

    def rows(html: str) -> list[dict]:
        return parse_rows(html, code, detail_base_url, name_prefix, with_title_and_date)

    total_pages = parse_total_pages(first_html)
    if total_pages is None:
        # 페이지 표시를 못 읽었는데 첫 페이지가 꽉 찼다면 사이트 구조가 바뀐 것이다 → 조용히 1페이지만 보지 않는다.
        if len(rows(first_html)) >= RECORD_COUNT:
            record_failure("list-pages", "페이지 수를 읽지 못함(사이트 구조 변경 가능)")
        total_pages = 1
    pages = min(total_pages, max_pages) if max_pages and max_pages > 0 else total_pages
    logger.info(f"총 {total_pages} 페이지 중 {pages} 페이지 수집")

    found: list[dict] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        if page == 1:
            html = first_html
        else:
            try:
                html = await asyncio.to_thread(get, page)
            except Exception as e:  # noqa: BLE001 - 한 페이지 실패는 기록하고 계속
                logger.error(f"❌ 목록 {page}페이지 수집 실패: {e}")
                record_failure(f"list-page:{page}", e)
                continue
        for item in rows(html):
            if item["jgmt_sn"] not in seen:
                seen.add(item["jgmt_sn"])
                found.append(item)

    logger.info(f"✅ 전체 URL 수집 완료: {len(found)}개")
    return found
