"""
law list_scraper — 국가법령정보 Open API(eflaw) 방식

2026-11-15 law.go.kr 화면 개편 뒤 목록 화면(lsAstSc.do)이 목록 대신 새 홈페이지를 돌려줘
Playwright 로 화면을 읽던 방식을 Open API 로 바꿨다.

target=eflaw(시행일 법령)는 nw=3 이 현행, nw=2 가 시행예정이다. 부처(org)마다 둘을 합친다.
상세 수집은 그대로 화면(lsInfoP.do?lsiSeq=…&efYd=…)을 읽으므로 URL 모양은 바꾸지 않는다.
"""

import asyncio
import os
import re
import sys
from datetime import date

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.lawgokr_openapi import ORG_CODES, search
from scripts.core.run_report import record_failure

logger = get_logger(__name__, scraper_type='law')

# nw: 3=현행, 2=시행예정
VERSION_KINDS = (("3", "현행"), ("2", "시행예정"))


def _item(row: dict, dept_name: str, today: str) -> dict | None:
    lsi_seq = str(row.get("법령일련번호") or "")
    efy = str(row.get("시행일자") or "")
    law_name = (row.get("법령명한글") or "").strip()
    if not lsi_seq or not re.fullmatch(r"\d{8}", efy):
        return None
    safe_name = re.sub(r'[\\/*?:"<>|]', "", law_name).strip() or f"law_{lsi_seq}"
    return {
        "name": safe_name,
        "url": f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={lsi_seq}&efYd={efy}",
        "effective": efy,
        "dept_name": dept_name,
        "law_id": law_name,   # 같은 법령의 버전 추적용 (법령명 = 그룹 키)
        "is_upcoming": efy > today,
    }


def collect_items(max_pages_arg: int | None = None, get=None) -> list:
    today = date.today().strftime("%Y%m%d")
    urls_found, seen = [], set()
    for org_code, dept_name in ORG_CODES:
        for nw, label in VERSION_KINDS:
            try:
                rows = list(search("eflaw", {"org": org_code, "nw": nw}, row_keys=("law",),
                                   max_pages=max_pages_arg, get=get))
            except Exception as e:  # noqa: BLE001 - 한 부처·종류 실패는 기록하고 나머지를 모은다
                logger.error(f"[{dept_name}] {label} 목록 실패: {e}")
                record_failure(f"list-org:{org_code}:{label}", e)
                continue
            added = 0
            for row in rows:
                item = _item(row, dept_name, today)
                if item and item["url"] not in seen:
                    seen.add(item["url"])
                    urls_found.append(item)
                    added += 1
            logger.info(f"[{dept_name}] {label} {added}건")
    return urls_found


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """소관 부처(ORG_CODES)의 현행·시행예정 법령 상세 URL 목록.

    Returns:
        list: [{"name", "url", "effective", "dept_name", "law_id", "is_upcoming"}, ...]
    """
    logger.info("법령 목록 수집 시작 (Open API eflaw)")
    urls_found = await asyncio.to_thread(collect_items, max_pages_arg)
    upcoming = sum(1 for it in urls_found if it["is_upcoming"])
    logger.info(f"✅ 법령 URL 수집 완료: {len(urls_found)}건 (시행예정 {upcoming}건)")
    return urls_found
