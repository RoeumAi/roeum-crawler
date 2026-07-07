import math
import os
import re
import sys
import time
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')

LIST_URL = "https://www.law.go.kr/LSW/cgmExpcAstScListR.do"
DETAIL_BASE = "https://www.law.go.kr/LSW/"
PAGE_SIZE = 50
MOEL_CODE = "1492000"


def _fetch_page(page_index: int, dept_code: str = MOEL_CODE) -> str:
    params = {
        "lsNm": "",
        "lsFdCd": "",
        "lsClsCd": "",
        "cptOfi": dept_code,
        "chrIdx": "0",
        "pageIndex": str(page_index),
        "sortBy": "LS_NM_KO",
        "sortType": "",
        "mode": "",
        "cptOfiDivCd": "",
    }
    resp = requests.post(LIST_URL, data=params, timeout=30)
    resp.raise_for_status()
    return resp.text


def _parse_total(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one(".tit2")
    text = title.get_text(" ", strip=True) if title else ""
    match = re.search(r"([0-9,]+)", text)
    return int(match.group(1).replace(",", "")) if match else 0


def _parse_items(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()

    for anchor in soup.select("a[href*='cgmExpcInfoP.do']"):
        href = unescape(anchor.get("href") or "")
        match = re.search(r"cgmExpcDatSeq=(\d+).*?ofiClsCd=(\d+)", href)
        if not match:
            continue

        doc_seq, ofi_cls_cd = match.groups()
        if doc_seq in seen:
            continue
        seen.add(doc_seq)

        detail_url = urljoin(DETAIL_BASE, href)
        items.append({
            "name": f"interpretation_{doc_seq}",
            "url": detail_url,
            "doc_seq": doc_seq,
            "ofi_cls_cd": ofi_cls_cd,
        })

    return items


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """Fetch MOEL-related central ministry interpretation detail URLs."""
    logger.info(f"중앙부처 1차 해석 목록 수집 시작 (참조 URL: {start_url})")

    first_html = _fetch_page(1)
    total_items = _parse_total(first_html)
    total_pages = math.ceil(total_items / PAGE_SIZE) if total_items else 1
    pages_to_crawl = total_pages
    if max_pages_arg is not None and 0 < max_pages_arg < total_pages:
        pages_to_crawl = max_pages_arg

    urls_found = []
    seen_doc_ids = set()

    for page_num in range(1, pages_to_crawl + 1):
        html = first_html if page_num == 1 else _fetch_page(page_num)
        page_items = _parse_items(html)
        for item in page_items:
            if item["doc_seq"] in seen_doc_ids:
                continue
            seen_doc_ids.add(item["doc_seq"])
            urls_found.append(item)
        logger.info(f"페이지 {page_num}/{pages_to_crawl}: {len(page_items)}개, 누적 {len(urls_found)}개")
        if page_num < pages_to_crawl:
            time.sleep(0.1)

    logger.info(f"✅ 중앙부처 1차 해석 URL 수집 완료: 목록 {total_items}행, 고유 문서 {len(urls_found)}개")
    return urls_found


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="중앙부처 1차 해석 URL 수집기")
    parser.add_argument("--max_pages", type=int, default=None, help="최대 페이지 수")
    args = parser.parse_args()

    urls = asyncio.run(fetch_urls("https://www.law.go.kr/cgmExpcAstSc.do", args.max_pages))
    print(f"\n수집된 URL: {len(urls)}개")
    for i, item in enumerate(urls[:5], 1):
        print(f"{i}. {item['name']}: {item['url']}")
