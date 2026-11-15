"""
constitutional_decc 목록 크롤러 — 국가법령정보 Open API(detc) 방식

2026-11-15 law.go.kr 화면 개편으로 소관부처 분류 목록(detcAstScListR.do)이 목록을 돌려주지 않아
Open API 로 바꿨다. detc 는 참조 법령(JO) 조건을 받지 않아, 소관 법령 이름 검색으로 후보를 모은 뒤
사건명·심판대상조문·참조조문에 소관 법령 이름이 나오는 것만 남긴다(scripts.core.lawgokr_openapi.collect_scoped_items).

상세 수집은 그대로 화면(detcInfoP.do?detcSeq=…)을 읽으므로 URL 모양은 바꾸지 않는다.
"""
import asyncio
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.lawgokr_openapi import ScopedTarget, collect_scoped_items
from scripts.core.run_report import record_failure

logger = get_logger(__name__, scraper_type='constitutional_decc')

SPEC = ScopedTarget(
    target="detc", collection="constitutional_decc", id_key="헌재결정례일련번호", title_key="사건명",
    detail_fields=("사건명", "심판대상조문", "참조조문"), name_prefix="detc",
    detail_url="https://www.law.go.kr/LSW/detcInfoP.do?mode=1&detcSeq={seq}",
)


def collect_items(get=None, laws: dict | None = None, store=None) -> list:
    return collect_scoped_items(SPEC, get=get, laws=laws, store=store, on_failure=record_failure)


async def fetch_urls(start_url: str, max_pages_arg: int | None):
    """소관 법령을 다룬 헌재결정례 상세 URL 목록. start_url·max_pages_arg 는 쓰지 않는다(호출 계약 유지용)."""
    logger.info("헌재결정례 목록 수집 시작 (Open API detc, 소관 법령 이름)")
    items = await asyncio.to_thread(collect_items)
    logger.info(f"총 {len(items)}개 URL 수집 완료")
    return items
