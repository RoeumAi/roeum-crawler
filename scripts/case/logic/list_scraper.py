"""
case list_scraper — 국가법령정보 Open API(prec) 방식

2026-11-15 law.go.kr 화면 개편으로 소관부처 분류 목록(precAstScListR.do)이 목록을 돌려주지 않아
Open API 로 바꿨다. Open API 에는 '소관부처 분류'가 없어 참조 법령(JO) 조건을 쓴다:
고용노동부 소관 현행 법령마다 그 법령을 참조한 판례를 받아 합친다.
(11-15 실측: 옛 분류로 받은 판례 17,580건 중 99%, 2025년 이후는 100% 포함)

상세 수집은 그대로 화면(precInfoP.do?precSeq=…)을 읽으므로 URL 모양은 바꾸지 않는다.
"""
import asyncio
import concurrent.futures as cf
import os
import re
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.lawgokr_openapi import MAX_WORKERS, jurisdiction_laws, search
from scripts.core.run_report import record_failure

logger = get_logger(__name__, scraper_type='case')

DETAIL_URL = "https://www.law.go.kr/LSW/precInfoP.do?mode=0&precSeq={seq}"


def _item(row: dict) -> dict | None:
    seq = str(row.get("판례일련번호") or "")
    if not seq.isdigit():
        return None
    name = re.sub(r'[\\/*?:"<>|]', "", (row.get("사건명") or "").strip()).strip()
    return {"name": name or f"case_{seq}", "url": DETAIL_URL.format(seq=seq)}


def collect_items(max_pages_arg: int | None = None, get=None, laws: dict | None = None) -> list:
    kwargs = {"get": get} if get else {}
    laws = laws or jurisdiction_laws(**kwargs)

    def by_law(name: str) -> list:
        return list(search("prec", {"JO": name}, max_pages=max_pages_arg, **kwargs))

    items, seen = [], set()
    with cf.ThreadPoolExecutor(MAX_WORKERS) as pool:
        futures = {pool.submit(by_law, name): name for name in laws["current"]}
        for future in cf.as_completed(futures):
            name = futures[future]
            try:
                rows = future.result()
            except Exception as e:  # noqa: BLE001 - 법령 하나 실패는 기록하고 나머지를 모은다
                logger.warning(f"판례 목록 실패({name}): {e}")
                record_failure(f"list-law:{name}", e)
                continue
            for row in rows:
                item = _item(row)
                if item and item["url"] not in seen:
                    seen.add(item["url"])
                    items.append(item)
    return items


async def fetch_urls(start_url: str, max_pages_arg: int | None):
    """소관 법령을 참조한 판례 상세 URL 목록. start_url 은 쓰지 않는다(호출 계약 유지용)."""
    logger.info("판례 목록 수집 시작 (Open API prec, 참조 법령 조건)")
    items = await asyncio.to_thread(collect_items, max_pages_arg)
    logger.info(f"URL 수집 완료: {len(items)}건 unique")
    return items
