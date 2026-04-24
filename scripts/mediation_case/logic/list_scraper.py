import asyncio
import json
import re
import os
import sys
from typing import List

from curl_cffi import requests
from bs4 import BeautifulSoup

# 프로젝트 루트 경로 설정
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='mediation_case')

LIST_ENDPOINT = "https://nlrc.go.kr/nlrc/mainCase/mainJudgment/list.do"
DETAIL_BASE_URL = "https://nlrc.go.kr/nlrc/mainCase/mediatioin/detail.do"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do"
}


def _post_list_html(page_index: int, record_count: int = 10) -> str:
    """리스트 페이지 POST 요청"""
    data = {
        "jgmtDcsnSeCd": "66",  # mediation case code (66), judgment is 65
        "dataSeCd": "",
        "pageIndex": str(page_index),
        "recordCountPerPage": str(record_count),
        "likeColumn": "TTL",
        "likeCondition": ""
    }
    response = requests.post(LIST_ENDPOINT, data=data, headers=DEFAULT_HEADERS, timeout=30, impersonate="chrome110")
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


def _parse_rows(html: str) -> List[dict]:
    """리스트 항목 파싱 - jgmtSn 속성 사용 (judgment와 동일)"""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr[data-event='click-detail'][data-jgmt-sn]")
    items = []
    for row in rows:
        jgmt_sn = (row.get("data-jgmt-sn") or "").strip()
        jgmt_dcsn_se_cd = (row.get("data-jgmt-dcsn-se-cd") or "66").strip()
        title_el = row.select_one("td.al a")
        title = title_el.get_text(strip=True) if title_el else ""
        if not jgmt_sn:
            continue

        safe_name = re.sub(r'[\\/*?:"<>|]', "", title).strip() if title else f"mediation_{jgmt_sn}"
        detail_url = f"{DETAIL_BASE_URL}?jgmtSn={jgmt_sn}&jgmtDcsnSeCd={jgmt_dcsn_se_cd}"

        items.append({
            "name": safe_name,
            "url": detail_url,
            "jgmt_sn": jgmt_sn,
            "jgmt_dcsn_se_cd": jgmt_dcsn_se_cd
        })

    return items


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """
    조정사건례 목록 페이지를 순회하며 상세 페이지 URL을 추출하여 반환합니다.

    Args:
        start_url: 시작 URL (로그용)
        max_pages_arg: 최대 페이지 수 (None이면 전체)

    Returns:
        list: [{"name": "문서명", "url": "상세페이지URL", "mdt_sn": "ID"}, ...]
    """
    logger.info(f"시작 페이지 확인: {start_url}")

    try:
        first_html = await asyncio.to_thread(_post_list_html, 1)
        total_pages = _parse_total_pages(first_html)
        logger.info(f"총 {total_pages} 페이지를 확인했습니다.")

        pages_to_crawl = total_pages
        if max_pages_arg is not None and 0 < max_pages_arg < total_pages:
            pages_to_crawl = max_pages_arg
            logger.info(f"사용자 설정에 따라 최대 {pages_to_crawl} 페이지만 크롤링합니다.")

        urls_found = []
        seen_ids = set()

        for page_num in range(1, pages_to_crawl + 1):
            logger.info(f"--- 페이지 {page_num} / {pages_to_crawl} 처리 중 ---")

            html = first_html if page_num == 1 else await asyncio.to_thread(_post_list_html, page_num)
            items = _parse_rows(html)
            logger.info(f"페이지 {page_num}에서 {len(items)}개의 항목을 발견했습니다.")

            for item in items:
                doc_id = item.get("jgmt_sn")  # Changed from mdt_sn to jgmt_sn
                if doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)
                urls_found.append(item)

        logger.info(f"✅ 전체 URL 수집 완료: {len(urls_found)}개")
        return urls_found

    except Exception as e:
        logger.error(f"❌ URL 수집 중 오류 발생: {e}", exc_info=True)
        return []


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="조정사건례 URL 수집기")
    parser.add_argument("--max_pages", type=int, default=None, help="최대 페이지 수")
    args = parser.parse_args()

    urls = asyncio.run(fetch_urls("https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do", args.max_pages))
    print(f"\n수집된 URL: {len(urls)}개")
    for i, url_item in enumerate(urls[:5], 1):
        print(f"{i}. {url_item['name']}: {url_item['url']}")
