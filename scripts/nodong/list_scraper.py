"""
nodong.kr 행정해석 목록 스크래퍼
- URL: https://www.nodong.kr/index.php?mid=interpretation&page={N}
- 목록 선택자: table.bd_lst tbody tr td.title a.hx
- 총 페이지: 1~25 (하드코딩, 사이트에 총 건수 표시 없음)
"""
import re
import time
import os
import sys

import requests
from bs4 import BeautifulSoup

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')

BASE_URL = "https://www.nodong.kr/index.php"
TOTAL_PAGES = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.nodong.kr/interpretation",
}


def _fetch_list_page(page: int) -> list[dict]:
    """목록 페이지 1장을 가져와 document_srl 목록 반환"""
    params = {"mid": "interpretation", "page": page}
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("table.bd_lst tbody tr td.title a.hx")

    items = []
    for a in rows:
        href = a.get("href", "")
        m = re.search(r"document_srl=(\d+)", href)
        if not m:
            continue
        srl = m.group(1)
        title = a.get_text(strip=True)
        safe_name = re.sub(r'[\\/*?:"<>|]', "", title).strip() if title else f"nodong_{srl}"
        items.append({
            "name": safe_name,
            "url": f"https://www.nodong.kr/index.php?mid=interpretation&document_srl={srl}",
            "doc_srl": srl,
        })
    return items


def fetch_all_urls(max_pages: int | None = None) -> list[dict]:
    """
    전체 목록 페이지를 순회하며 URL 목록 반환

    Returns:
        [{"name": str, "url": str, "doc_srl": str}, ...]
    """
    pages_to_crawl = min(max_pages, TOTAL_PAGES) if max_pages else TOTAL_PAGES
    logger.info(f"nodong.kr 행정해석 목록 수집 시작 (총 {pages_to_crawl}페이지)")

    urls_found = []
    seen = set()

    for page in range(1, pages_to_crawl + 1):
        logger.info(f"페이지 {page}/{pages_to_crawl} 수집 중...")
        try:
            items = _fetch_list_page(page)
        except Exception as e:
            logger.error(f"페이지 {page} 수집 실패: {e}")
            continue

        for item in items:
            if item["doc_srl"] in seen:
                continue
            seen.add(item["doc_srl"])
            urls_found.append(item)

        logger.info(f"페이지 {page}: {len(items)}개 발견 (누계 {len(urls_found)}개)")
        if page < pages_to_crawl:
            time.sleep(0.3)

    logger.info(f"✅ 목록 수집 완료: 총 {len(urls_found)}개")
    return urls_found


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max_pages", type=int, default=None)
    args = parser.parse_args()

    items = fetch_all_urls(args.max_pages)
    print(f"수집된 항목: {len(items)}개")
    for item in items[:5]:
        print(f"  {item['doc_srl']} | {item['name'][:40]}")
