import asyncio
import json
import math
import re
import argparse
import os
import sys
import time

import requests
from bs4 import BeautifulSoup

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='case')

AJAX_URL = "https://www.law.go.kr/LSW/precAstScListR.do"
PAGE_SIZE = 50

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.law.go.kr/",
}


def _post_list_page(cpt_ofi_cd: str, page_index: int) -> BeautifulSoup:
    resp = requests.post(
        AJAX_URL,
        data={"cptOfiCd": cpt_ofi_cd, "chrIdx": "0", "pageIndex": str(page_index)},
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _get_total_pages(soup: BeautifulSoup) -> int:
    el = soup.select_one(".tit2")
    if el:
        m = re.search(r"\((\d+)/(\d+)\)", el.get_text())
        if m:
            return int(m.group(2))
    # fallback: count links on page → 1 page
    return 1


def _extract_urls(soup: BeautifulSoup, seen: set) -> list:
    items = []
    for a in soup.select('a[href*="precInfoP"]'):
        href = a.get("href", "")
        m = re.search(r"precSeq=(\d+)", href)
        if not m:
            continue
        prec_seq = m.group(1)
        if prec_seq in seen:
            continue
        seen.add(prec_seq)
        if href.startswith("http"):
            full_url = href
        elif href.startswith("/"):
            full_url = "https://www.law.go.kr" + href
        else:
            full_url = "https://www.law.go.kr/LSW/" + href
        name = re.sub(r'[\\/*?:"<>|]', "", a.get_text(strip=True)).strip()
        items.append({"name": name or f"case_{prec_seq}", "url": full_url})
    return items


async def fetch_urls(start_url: str, max_pages_arg: int | None):
    """판례 목록을 AJAX API로 순회하며 상세 페이지 URL을 반환합니다."""
    # start_url에서 cptOfiCd 추출 (없으면 기본값 사용)
    m = re.search(r"cptOfiCd=(\d+)", start_url or "")
    cpt_ofi_cd = m.group(1) if m else "1492000"

    urls_found = []
    seen = set()

    soup1 = _post_list_page(cpt_ofi_cd, 1)
    total_pages = _get_total_pages(soup1)
    logger.info(f"총 {total_pages}페이지")

    if max_pages_arg:
        total_pages = min(total_pages, max_pages_arg)
        logger.info(f"최대 {total_pages}페이지로 제한")

    urls_found.extend(_extract_urls(soup1, seen))

    for page_idx in range(2, total_pages + 1):
        if page_idx % 50 == 0:
            logger.info(f"페이지 {page_idx}/{total_pages}, 현재 {len(urls_found)}건")
        try:
            soup = _post_list_page(cpt_ofi_cd, page_idx)
            urls_found.extend(_extract_urls(soup, seen))
        except Exception as e:
            logger.warning(f"페이지 {page_idx} 오류: {e}")
        time.sleep(0.15)

    logger.info(f"URL 수집 완료: {len(urls_found)}건 unique")
    return urls_found


async def main():
    parser = argparse.ArgumentParser(description="판례 목록에서 상세 URL을 추출합니다.")
    parser.add_argument("start_url", help="cptOfiCd가 포함된 시작 URL")
    parser.add_argument("-o", "--output", required=True, help="출력 파일 경로 (.jsonl)")
    parser.add_argument("--max_pages", type=int, default=None, help="최대 페이지 수")
    args = parser.parse_args()

    urls = await fetch_urls(args.start_url, args.max_pages)

    if urls:
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            for item in urls:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        logger.info(f"총 {len(urls)}개 URL → '{args.output}'")


if __name__ == "__main__":
    asyncio.run(main())
