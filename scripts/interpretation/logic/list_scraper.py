"""중앙부처 1차 해석(고용노동부) 목록 — 국가법령정보 Open API(moelCgmExpc) 방식.

2026-11-15 law.go.kr 화면 개편으로 목록 화면(cgmExpcAstScListR.do)이 목록을 돌려주지 않아 Open API 로 바꿨다.
moelCgmExpc 는 고용노동부 해석만 돌려준다(옛 화면 목록에는 환경부·국토교통부 해석이 섞여 들어왔다).
Open API 의 법령해석일련번호는 화면의 cgmExpcDatSeq 와 같은 번호라 기존 문서(doc_id)와 그대로 이어진다.

상세는 Open API 로 받는다(scraper.py). url 은 사람이 여는 원문 화면 주소로 남긴다.
"""
import asyncio
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.core.lawgokr_openapi import search
from scripts.core.run_report import record_failure
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')

TARGET = "moelCgmExpc"
# 화면 원문 주소. ofiClsCd=350101(중앙부처 1차 해석)이 있어야 열린다.
DETAIL_URL = "https://www.law.go.kr/LSW/cgmExpcInfoP.do?mode=2&cgmExpcDatSeq={seq}&ofiClsCd=350101"


def collect_items(max_pages_arg: int | None = None, get=None) -> list:
    kwargs = {"get": get} if get else {}
    items, seen = [], set()

    def page_failed(page: int, exc: Exception) -> None:
        # 중간 쪽 하나 실패는 기록하고 나머지 쪽을 모은다(첫 쪽 실패는 목록 전체 실패로 올라간다)
        logger.error(f"❌ 목록 {page}페이지 수집 실패: {exc}")
        record_failure(f"list-page:{page}", exc)

    for row in search(TARGET, row_keys=("cgmExpc",), max_pages=max_pages_arg, on_page_error=page_failed, **kwargs):
        seq = str(row.get("법령해석일련번호") or "")
        if not seq.isdigit() or seq in seen:
            continue
        seen.add(seq)
        items.append({
            "name": f"interpretation_{seq}",
            "url": DETAIL_URL.format(seq=seq),
            "doc_seq": seq,
            "ofi_cls_cd": "350101",
        })
    return items


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """고용노동부 1차 해석 목록. 첫 쪽을 끝내 못 받으면 예외를 올려 목록 전체 실패로 보고된다."""
    logger.info("중앙부처 1차 해석 목록 수집 시작 (Open API moelCgmExpc)")
    items = await asyncio.to_thread(collect_items, max_pages_arg)
    logger.info(f"✅ 중앙부처 1차 해석 URL 수집 완료: 고유 문서 {len(items)}개")
    return items
