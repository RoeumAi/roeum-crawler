"""
legislation_expc 목록 크롤러 — 국가법령정보 Open API(expc) 방식

2026-11-15 law.go.kr 화면 개편으로 소관부처 분류 목록(expcAstScListR.do)이 목록을 돌려주지 않아
Open API 로 바꿨다. expc 는 참조 법령(JO) 조건을 받지 않아, 소관 법령 이름 검색으로 후보를 모은 뒤
안건명·질의요지에 소관 법령 이름이 나오는 것만 남긴다(scripts.core.lawgokr_openapi.collect_scoped_items).

상세 수집은 그대로 화면(expcInfoP.do?expcSeq=…)을 읽으므로 URL 모양은 바꾸지 않는다.
"""
import asyncio
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.lawgokr_openapi import ScopedTarget, collect_scoped_items
from scripts.core.run_report import record_failure

logger = get_logger(__name__, scraper_type='legislation_expc')

SPEC = ScopedTarget(
    target="expc", collection="legislation_expc", id_key="법령해석례일련번호", title_key="안건명",
    detail_fields=("안건명", "질의요지"), name_prefix="expc",
    detail_url="https://www.law.go.kr/LSW/expcInfoP.do?mode=2&expcSeq={seq}",
)


def collect_items(get=None, laws: dict | None = None, store=None) -> list:
    return collect_scoped_items(SPEC, get=get, laws=laws, store=store, on_failure=record_failure)


async def fetch_urls(start_url: str, max_pages_arg: int | None):
    """소관 법령을 다룬 법제처해석례 상세 URL 목록. start_url·max_pages_arg 는 쓰지 않는다(호출 계약 유지용)."""
    logger.info("법제처해석례 목록 수집 시작 (Open API expc, 소관 법령 이름)")
    items = await asyncio.to_thread(collect_items)
    logger.info(f"총 {len(items)}개 URL 수집 완료")
    return items
