"""
심의결정례 목록 스크래퍼

nlrc.go.kr의 mainJudgment/list.do에서 jgmtDcsnSeCd=67 (심의결정례) 목록을 수집합니다.

심의결정례는 별도 상세 페이지가 없으므로 목록의 제목과 날짜가 곧 전체 콘텐츠입니다.
이를 반영하여 title과 registered_at을 URL 아이템 dict에 포함시켜 반환합니다.
"""

import asyncio
import re
from typing import List, Dict, Optional
import sys
import os

import requests
from bs4 import BeautifulSoup

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='decision')

LIST_ENDPOINT = "https://nlrc.go.kr/nlrc/mainCase/mainJudgment/list.do"
DECISION_CODE = "67"  # jgmtDcsnSeCd=67 = 심의결정례

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do"
}


def _post_list_html(page_index: int, record_count: int = 10) -> str:
    """목록 페이지 POST 요청"""
    data = {
        "jgmtDcsnSeCd": DECISION_CODE,
        "dataSeCd": "",
        "pageIndex": str(page_index),
        "recordCountPerPage": str(record_count),
        "likeColumn": "TTL",
        "likeCondition": ""
    }
    response = requests.post(LIST_ENDPOINT, data=data, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def _parse_total_pages(html: str) -> int:
    """전체 페이지 수 파싱"""
    soup = BeautifulSoup(html, "html.parser")
    page_indexes = []
    for el in soup.select(".bbs_pagerA [data-pageindex]"):
        value = (el.get("data-pageindex") or "").strip()
        if value.isdigit():
            page_indexes.append(int(value))
    return max(page_indexes) if page_indexes else 1


def _parse_rows(html: str) -> List[Dict]:
    """
    목록 항목 파싱

    심의결정례는 상세 페이지가 없고 목록의 제목·날짜가 전체 콘텐츠이므로
    title과 registered_at을 아이템에 포함하여 반환합니다.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr[data-event='click-detail'][data-jgmt-sn]")
    items = []
    for row in rows:
        jgmt_sn = (row.get("data-jgmt-sn") or "").strip()
        jgmt_dcsn_se_cd = (row.get("data-jgmt-dcsn-se-cd") or DECISION_CODE).strip()

        tds = row.select("td")
        title_el = row.select_one("td.al a")
        title = title_el.get_text(strip=True) if title_el else ""

        # 날짜는 마지막 td에 위치
        registered_at = ""
        if len(tds) >= 3:
            registered_at = tds[-1].get_text(strip=True)

        if not jgmt_sn:
            continue

        safe_name = re.sub(r'[\\/*?:"<>|]', "", title).strip() if title else f"decision_{jgmt_sn}"
        # 심의결정례 상세 URL (서버 응답은 빈 페이지지만 doc_id 용도로 사용)
        detail_url = f"https://nlrc.go.kr/nlrc/mainCase/judgment/detail.do?jgmtSn={jgmt_sn}&jgmtDcsnSeCd={jgmt_dcsn_se_cd}"

        items.append({
            "name": safe_name,
            "url": detail_url,
            "jgmt_sn": jgmt_sn,
            "jgmt_dcsn_se_cd": jgmt_dcsn_se_cd,
            # 심의결정례는 제목·날짜가 전체 콘텐츠이므로 여기서 포함
            "title": title,
            "registered_at": registered_at,
        })

    return items


async def fetch_urls(start_url: str, max_pages_arg: Optional[int] = None) -> List[Dict]:
    """
    심의결정례 목록 전체 수집

    Args:
        start_url: 시작 URL (로그용, 실제 요청은 LIST_ENDPOINT POST 사용)
        max_pages_arg: 최대 페이지 수 (None이면 전체)

    Returns:
        list: [{"name", "url", "jgmt_sn", "jgmt_dcsn_se_cd", "title", "registered_at"}, ...]
    """
    logger.info(f"심의결정례 목록 수집 시작 (참조 URL: {start_url})")

    try:
        first_html = await asyncio.to_thread(_post_list_html, 1)
        total_pages = _parse_total_pages(first_html)
        logger.info(f"총 {total_pages} 페이지 확인")

        pages_to_crawl = total_pages
        if max_pages_arg is not None and 0 < max_pages_arg < total_pages:
            pages_to_crawl = max_pages_arg
            logger.info(f"최대 {pages_to_crawl} 페이지로 제한")

        urls_found = []
        seen_ids = set()

        for page_num in range(1, pages_to_crawl + 1):
            logger.info(f"--- 페이지 {page_num} / {pages_to_crawl} 처리 중 ---")
            html = first_html if page_num == 1 else await asyncio.to_thread(_post_list_html, page_num)
            items = _parse_rows(html)
            logger.info(f"페이지 {page_num}: {len(items)}개 항목 발견")

            for item in items:
                doc_id = item["jgmt_sn"]
                if doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)
                urls_found.append(item)

        logger.info(f"✅ 심의결정례 URL 수집 완료: {len(urls_found)}개")
        return urls_found

    except Exception as e:
        logger.error(f"❌ 심의결정례 URL 수집 실패: {e}", exc_info=True)
        return []


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="심의결정례 URL 수집기")
    parser.add_argument("--max_pages", type=int, default=None, help="최대 페이지 수")
    args = parser.parse_args()

    urls = asyncio.run(fetch_urls(
        "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do",
        args.max_pages
    ))
    print(f"\n수집된 URL: {len(urls)}개")
    for i, item in enumerate(urls[:5], 1):
        print(f"{i}. [{item['registered_at']}] {item['title'][:60]}")
