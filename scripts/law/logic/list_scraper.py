import math
import re
import time
import os
import sys
from datetime import date

import requests

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='law')

API_BASE = "https://www.law.go.kr/DRF/lawSearch.do"
OC = "inwoong100"
PAGE_SIZE = 100

# 수집 대상 소관부처 코드 → 부처명
ORG_CODES = {
    "1492000": "고용노동부",
    "1790365": "개인정보보호위원회",
}


def _fetch_org_page(org: str, page: int) -> dict:
    """단일 org의 특정 페이지 결과 반환 (items list, totalCnt)."""
    params = {
        "OC": OC, "target": "law", "type": "JSON",
        "page": page, "display": PAGE_SIZE, "query": "", "org": org,
    }
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json().get("LawSearch", {})
    return {
        "items": data.get("law", []),
        "totalCnt": int(data.get("totalCnt", 0)),
    }


def _fetch_upcoming_versions(ls_id: str, today: str) -> list:
    """법령ID로 시행예정(오늘 이후) 버전 목록을 반환합니다."""
    params = {
        "OC": OC, "target": "law", "type": "JSON",
        "page": 1, "display": 20, "LsId": ls_id,
    }
    try:
        resp = requests.get(API_BASE, params=params, timeout=20)
        resp.raise_for_status()
        items = resp.json().get("LawSearch", {}).get("law", [])
        return [it for it in items if (it.get("시행일자", "") or "") > today]
    except Exception as e:
        logger.warning(f"법령ID {ls_id} 시행예정 조회 실패: {e}")
        return []


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """
    법령 목록 API를 순회하며 상세 페이지 URL과 메타데이터를 반환합니다.

    - ORG_CODES에 정의된 각 소관부처의 현행 법령 수집
    - 각 법령의 시행예정(미래 efYd) 버전도 함께 수집
    - update 시 동일 법령의 구 버전은 upsert_with_change_detection이 is_active=false 처리

    Returns:
        list: [{"name", "url", "effective", "dept_name", "law_id", "is_upcoming"}, ...]
    """
    logger.info(f"법령 목록 API 수집 시작 (참조 URL: {start_url})")

    today = date.today().strftime("%Y%m%d")
    urls_found = []
    seen = set()  # lsiSeq 기준 중복 방지

    for org_code, dept_name in ORG_CODES.items():
        logger.info(f"[{dept_name}] org={org_code} 수집 시작")

        # 총 페이지 수 확인
        first = _fetch_org_page(org_code, 1)
        total_cnt = first["totalCnt"]
        total_pages = math.ceil(total_cnt / PAGE_SIZE)
        logger.info(f"[{dept_name}] 총 {total_cnt}건 ({total_pages}페이지)")

        pages_to_crawl = total_pages
        if max_pages_arg is not None and 0 < max_pages_arg < total_pages:
            pages_to_crawl = max_pages_arg
            logger.info(f"[{dept_name}] 최대 {pages_to_crawl}페이지로 제한")

        all_items = list(first["items"])
        for page_num in range(2, pages_to_crawl + 1):
            try:
                result = _fetch_org_page(org_code, page_num)
                all_items.extend(result["items"])
            except Exception as e:
                logger.error(f"[{dept_name}] 페이지 {page_num} 조회 실패: {e}")
            time.sleep(0.3)

        # 현행 버전 처리 + 시행예정 버전 조회
        upcoming_check_done = set()
        for item in all_items:
            mst = item.get("법령일련번호", "")
            efy = item.get("시행일자", "")
            name = item.get("법령명한글", "").strip()
            ls_id = item.get("법령ID", "")
            actual_dept = item.get("소관부처명", dept_name).strip()
            if not mst:
                continue

            # 현행 버전 추가
            if mst not in seen:
                seen.add(mst)
                safe_name = re.sub(r'[\\/*?:"<>|]', "", name).strip() or f"law_{mst}"
                is_upcoming = efy > today if efy else False
                urls_found.append({
                    "name": safe_name,
                    "url": f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={mst}&efYd={efy}",
                    "effective": efy,
                    "dept_name": actual_dept,
                    "law_id": ls_id,
                    "is_upcoming": is_upcoming,
                })

            # 시행예정 버전 — 법령ID 기준으로 1회만 조회
            if ls_id and ls_id not in upcoming_check_done:
                upcoming_check_done.add(ls_id)
                upcoming = _fetch_upcoming_versions(ls_id, today)
                for up in upcoming:
                    up_mst = up.get("법령일련번호", "")
                    up_efy = up.get("시행일자", "")
                    up_name = up.get("법령명한글", "").strip()
                    if not up_mst or up_mst in seen:
                        continue
                    seen.add(up_mst)
                    safe_up = re.sub(r'[\\/*?:"<>|]', "", up_name).strip() or f"law_{up_mst}"
                    urls_found.append({
                        "name": safe_up,
                        "url": f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={up_mst}&efYd={up_efy}",
                        "effective": up_efy,
                        "dept_name": up.get("소관부처명", dept_name).strip(),
                        "law_id": ls_id,
                        "is_upcoming": True,
                    })
                time.sleep(0.2)

    logger.info(f"✅ 법령 URL 수집 완료: {len(urls_found)}개 (현행+시행예정)")
    return urls_found
