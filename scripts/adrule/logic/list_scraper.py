"""행정규칙 목록 — 국가법령정보 Open API(admrul).

전체 행정규칙을 쪽마다 받아 고용노동부 소관만 남긴다(Open API 에 부처 조건이 없다).
요청·재시도·오류 판정은 scripts.core.lawgokr_openapi.search 를 쓴다(법제처 자료 목록 공통).
"""
import asyncio
import os
import re
import sys
from datetime import date

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.lawgokr_openapi import search
from scripts.core.run_report import record_failure

logger = get_logger(__name__, scraper_type='adrule')

DETAIL_URL = "https://www.law.go.kr/admRulInfoP.do?admRulSeq={seq}"


# 고용노동부 소관 행정규칙 판별 함수
# 부처명 변형 포함: 노동부(이전 명칭), 중앙노동위원회·노동위원회(고용노동부 산하),
# 고용노동부고객상담센터, 복수 부처 공동 고시(소관부처명에 "고용노동부" 포함)
def _is_valid_dept(dept: str) -> bool:
    dept = dept.strip()
    return "고용노동부" in dept or dept in {"노동부", "중앙노동위원회", "노동위원회"}


def collect_items(max_pages_arg: int | None = None, get=None) -> list:
    today = date.today().strftime("%Y%m%d")

    def page_failed(page: int, exc: Exception) -> None:
        logger.error(f"페이지 {page} 조회 실패: {exc}")
        record_failure(f"list-page:{page}", exc)

    urls_found, seen = [], set()
    for item in search("admrul", {"query": ""}, row_keys=("admrul",), max_pages=max_pages_arg,
                       get=get, on_page_error=page_failed):
        if not _is_valid_dept(item.get("소관부처명", "")):
            continue
        seq = item.get("행정규칙일련번호", "")
        if not seq:
            continue
        url = DETAIL_URL.format(seq=seq)
        if url in seen:
            continue
        seen.add(url)
        efy = item.get("시행일자", "")
        name = item.get("행정규칙명", "").strip()
        safe_name = re.sub(r'[\\/*?:"<>|]', "", name).strip() if name else f"adrule_{seq}"
        urls_found.append({
            "name": safe_name,
            "url": url,
            "effective": efy,  # 시행일자 — update 모드 변경 감지에 사용
            "is_upcoming": bool(efy) and efy > today,
        })
    return urls_found


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """
    행정규칙 목록 API를 순회하며 상세 페이지 URL과 시행일자를 반환합니다.

    Returns:
        list: [{"name": str, "url": str, "effective": str, "is_upcoming": bool}, ...]
        - url: https://www.law.go.kr/admRulInfoP.do?admRulSeq={행정규칙일련번호}
        - effective: 시행일자 (YYYYMMDD)
        - is_upcoming: 시행일자가 오늘보다 미래인지 여부 (law list_scraper와 동일 기준)
    """
    logger.info("행정규칙 목록 API 수집 시작 (Open API admrul)")
    urls_found = await asyncio.to_thread(collect_items, max_pages_arg)
    logger.info(f"✅ 행정규칙 URL 수집 완료: {len(urls_found)}개")
    return urls_found
