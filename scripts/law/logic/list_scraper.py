import math
import re
import time
import os
import sys

import requests

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='law')

API_BASE = "https://www.law.go.kr/DRF/lawSearch.do"
OC = "inwoong100"
PAGE_SIZE = 100
# org=1492000: 고용노동부 소관 법령만 반환 (cptOfiCd와 달리 실제 소관 부처 기준 필터)
ORG_CODE = "1492000"


def _fetch_page(page: int) -> list:
    params = {
        "OC": OC,
        "target": "law",
        "type": "JSON",
        "page": page,
        "display": PAGE_SIZE,
        "query": "",
        "org": ORG_CODE,
    }
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("LawSearch", {}).get("law", [])


def _total_pages() -> int:
    params = {
        "OC": OC,
        "target": "law",
        "type": "JSON",
        "page": 1,
        "display": PAGE_SIZE,
        "query": "",
        "org": ORG_CODE,
    }
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    total = int(data.get("LawSearch", {}).get("totalCnt", 0))
    return math.ceil(total / PAGE_SIZE)


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """
    법령 목록 API를 순회하며 상세 페이지 URL과 시행일자를 반환합니다.

    Returns:
        list: [{"name": str, "url": str, "effective": str}, ...]
        - url: https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={MST}&efYd={시행일자}
        - effective: 시행일자 (YYYYMMDD)
    """
    logger.info(f"법령 목록 API 수집 시작 (참조 URL: {start_url})")

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
            mst = item.get("법령일련번호", "")
            efy = item.get("시행일자", "")
            name = item.get("법령명한글", "").strip()
            if not mst:
                continue

            url = f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={mst}&efYd={efy}"
            if url in seen:
                continue
            seen.add(url)

            safe_name = re.sub(r'[\\/*?:"<>|]', "", name).strip() if name else f"law_{mst}"
            urls_found.append({
                "name": safe_name,
                "url": url,
                "effective": efy,  # 시행일자 — update 모드 변경 감지에 사용
                "dept_name": item.get("소관부처명", "").strip(),  # 소관부처명 — RAG 필터링용
            })

        if page_num < pages_to_crawl:
            time.sleep(0.3)  # API 과부하 방지

    logger.info(f"✅ 법령 URL 수집 완료: {len(urls_found)}개")
    return urls_found
