"""
legislation_expc 목록 크롤러
POST API 기반, Playwright 불필요
"""
import asyncio
import requests
from bs4 import BeautifulSoup
import re
import argparse
import os
import sys
import json

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='legislation_expc')

AJAX_URL = 'https://www.law.go.kr/LSW/expcAstScListR.do'
CPT_OFI = '1492000'
PAGE_SIZE = 50

def _post_list_page(page_index: int) -> BeautifulSoup:
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': 'https://www.law.go.kr/',
    }
    resp = requests.post(
        AJAX_URL,
        data={'cptOfi': CPT_OFI, 'chrIdx': '0', 'pageIndex': str(page_index)},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return BeautifulSoup(resp.text, 'html.parser')


def _get_total_count(soup: BeautifulSoup) -> int:
    for sel in ['.tit2 strong', '#totalCount', '.total strong']:
        el = soup.select_one(sel)
        if el:
            text = re.sub(r'[^\d]', '', el.get_text())
            if text:
                return int(text)
    # fallback: count links
    return len(soup.select('a[href*=expcInfoP]'))


async def fetch_urls(start_url: str, max_pages_arg: int | None):
    """목록 API를 순회하며 상세 페이지 URL을 반환합니다."""
    urls_found = []
    seen_urls = set()

    # 1페이지 → 총 건수 파악
    logger.info("1페이지 요청 중...")
    soup1 = _post_list_page(1)
    total_count = _get_total_count(soup1)
    total_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
    logger.info(f"총 {total_count}건 / {total_pages}페이지")

    if max_pages_arg:
        total_pages = min(total_pages, max_pages_arg)
        logger.info(f"최대 {total_pages}페이지로 제한")

    def extract_from_soup(s):
        for a in s.select(f'a[href*="expcInfoP"]'):
            href = a.get('href', '')
            if 'expcInfoP' not in href:
                continue
            if href.startswith('http'):
                full_url = href
            elif href.startswith('/'):
                full_url = 'https://www.law.go.kr' + href
            else:
                full_url = 'https://www.law.go.kr/LSW/' + href
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            name = re.sub(r'[\\/*?:"<>|]', '', a.get_text(strip=True)).strip()
            urls_found.append({'name': name, 'url': full_url})

    extract_from_soup(soup1)

    for page_idx in range(2, total_pages + 1):
        logger.info(f"페이지 {page_idx} / {total_pages} 요청 중...")
        try:
            soup = _post_list_page(page_idx)
            extract_from_soup(soup)
        except Exception as e:
            logger.error(f"페이지 {page_idx} 오류: {e}")

    logger.info(f"총 {len(urls_found)}개 URL 수집 완료")
    return urls_found


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("start_url", nargs="?", default=AJAX_URL)
    parser.add_argument("-o", "--output", default="data/output/legislation_expc_urls.jsonl")
    parser.add_argument("--max_pages", type=int, default=None)
    args = parser.parse_args()

    urls = asyncio.run(fetch_urls(args.start_url, args.max_pages))
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for item in urls:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"저장 완료: {len(urls)}건 → {args.output}")
