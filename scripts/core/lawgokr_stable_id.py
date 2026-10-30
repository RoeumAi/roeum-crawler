"""
law.go.kr 상세 API로 법령/행정규칙의 안정 ID(법령ID/행정규칙ID)를 조회한다.

같은 법령·행정규칙이라도 개정될 때마다 새 doc_id(MST/일련번호)가 발급되므로,
버전을 그룹핑하려면 이 안정 ID가 필요하다.
"""

from typing import Optional

import requests

from scripts.core.run_report import call_with_retry
from scripts.core.config import LAWGO_OC
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='lawgokr_stable_id')

API_BASE = "https://www.law.go.kr/DRF/lawService.do"
OC = LAWGO_OC  # 정의는 scripts/core/config.py
TIMEOUT_SECONDS = 10


def fetch_record(target: str, doc_id: str) -> Optional[dict]:
    """Open API 상세(JSON). target=law 는 MST, admrul 은 ID(일련번호). 실패 시 None.

    멀쩡한 문서에도 잠깐 404 를 줄 때가 있어(10-29 법령 2건) 몇 번 더 시도한다."""
    key = "MST" if target == "law" else "ID"
    params = {"OC": OC, "target": target, key: doc_id, "type": "JSON"}

    def fetch():
        resp = requests.get(API_BASE, params=params, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()

    try:
        return call_with_retry(fetch)
    except Exception as e:
        logger.warning(f"Open API 조회 실패 ({target} {key}={doc_id}): {e}")
        return None


def stable_law_id(record: Optional[dict]) -> Optional[str]:
    law_id = ((record or {}).get("법령") or {}).get("기본정보", {}).get("법령ID")
    return str(law_id) if law_id else None


def stable_adrule_id(record: Optional[dict]) -> Optional[str]:
    adrule_id = ((record or {}).get("AdmRulService") or {}).get("행정규칙기본정보", {}).get("행정규칙ID")
    return str(adrule_id) if adrule_id else None


def fetch_stable_law_id(doc_id: str) -> Optional[str]:
    """MST(doc_id)로 법령ID(안정 ID)를 조회한다. 실패 시 None."""
    return stable_law_id(fetch_record("law", doc_id))


def fetch_stable_adrule_id(doc_id: str) -> Optional[str]:
    """ID(doc_id, 행정규칙일련번호)로 행정규칙ID(안정 ID)를 조회한다. 실패 시 None."""
    return stable_adrule_id(fetch_record("admrul", doc_id))
