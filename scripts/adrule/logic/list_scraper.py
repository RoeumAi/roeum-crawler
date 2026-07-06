import math
import re
import time
import os
import sys

import requests

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='adrule')

API_BASE = "https://www.law.go.kr/DRF/lawSearch.do"
OC = "inwoong100"
PAGE_SIZE = 100
TARGET_DEPT = "고용노동부"  # 소관부처명 필터 — 이 부처 행정규칙만 수집


def _fetch_page(page: int) -> list:
    params = {
        "OC": OC,
        "target": "admrul",
        "type": "JSON",
        "page": page,
        "display": PAGE_SIZE,
        "query": "",
    }
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("AdmRulSearch", {}).get("admrul", [])
    # 소관부처명 필터 — 고용노동부 행정규칙만 반환
    return [item for item in items if item.get("소관부처명", "").strip() == TARGET_DEPT]


def _total_pages() -> int:
    # API의 totalCnt는 전체 행정규칙 수이므로, 실제 페이지 수를 그대로 사용.
    # 각 페이지에서 소관부처명 필터 후 수집하므로 전체 페이지를 순회해야 한다.
    params = {
        "OC": OC,
        "target": "admrul",
        "type": "JSON",
        "page": 1,
        "display": PAGE_SIZE,
        "query": "",
    }
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    total = int(data.get("AdmRulSearch", {}).get("totalCnt", 0))
    return math.ceil(total / PAGE_SIZE)


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """
    행정규칙 목록 API를 순회하며 상세 페이지 URL과 시행일자를 반환합니다.

    Returns:
        list: [{"name": str, "url": str, "effective": str}, ...]
        - url: https://www.law.go.kr/admRulInfoP.do?admRulSeq={행정규칙일련번호}
        - effective: 시행일자 (YYYYMMDD)
    """
    logger.info(f"행정규칙 목록 API 수집 시작 (참조 URL: {start_url})")

    total_pages = _total_pages()
    logger.info(f"총 {total_pages} 페이지 확인")

    pages_to_crawl = total_pages
    if max_pages_arg is not None and 0 < max_pages_arg < total_pages:
        pages_to_crawl = max_pages_arg
        logger.info(f"최대 {pages_to_crawl} 페이지로 제한")

    urls_found = []
    seen = set()

    for page_num in range(1, pages_to_crawl + 1):
        logger.info(f"--- 페이지 {page_num} / {pages_to_crawl} 처리 중 ---")
        try:
            items = _fetch_page(page_num)
        except Exception as e:
            logger.error(f"페이지 {page_num} 조회 실패: {e}")
            continue

        logger.info(f"페이지 {page_num}: {len(items)}개 항목 발견")

        for item in items:
            seq = item.get("행정규칙일련번호", "")
            efy = item.get("시행일자", "")
            name = item.get("행정규칙명", "").strip()
            if not seq:
                continue

            url = f"https://www.law.go.kr/admRulInfoP.do?admRulSeq={seq}"
            if url in seen:
                continue
            seen.add(url)

            safe_name = re.sub(r'[\\/*?:"<>|]', "", name).strip() if name else f"adrule_{seq}"
            urls_found.append({
                "name": safe_name,
                "url": url,
                "effective": efy,  # 시행일자 — update 모드 변경 감지에 사용
            })

        if page_num < pages_to_crawl:
            time.sleep(0.3)

    logger.info(f"✅ 행정규칙 URL 수집 완료: {len(urls_found)}개")
    return urls_found
