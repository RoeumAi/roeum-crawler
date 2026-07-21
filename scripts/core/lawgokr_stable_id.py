"""
law.go.kr 상세 API로 법령/행정규칙의 안정 ID(법령ID/행정규칙ID)를 조회한다.

같은 법령·행정규칙이라도 개정될 때마다 새 doc_id(MST/일련번호)가 발급되므로,
버전을 그룹핑하려면 이 안정 ID가 필요하다.
"""

from typing import Optional

import requests

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='lawgokr_stable_id')

API_BASE = "https://www.law.go.kr/DRF/lawService.do"
OC = "inwoong100"
TIMEOUT_SECONDS = 10


def fetch_stable_law_id(doc_id: str) -> Optional[str]:
    """MST(doc_id)로 법령ID(안정 ID)를 조회한다. 실패 시 None."""
    params = {"OC": OC, "target": "law", "MST": doc_id, "type": "JSON"}
    try:
        resp = requests.get(API_BASE, params=params, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        law_id = data.get("법령", {}).get("기본정보", {}).get("법령ID")
        return str(law_id) if law_id else None
    except Exception as e:
        logger.warning(f"법령ID 조회 실패 (MST={doc_id}): {e}")
        return None


def fetch_stable_adrule_id(doc_id: str) -> Optional[str]:
    """ID(doc_id, 행정규칙일련번호)로 행정규칙ID(안정 ID)를 조회한다. 실패 시 None."""
    params = {"OC": OC, "target": "admrul", "ID": doc_id, "type": "JSON"}
    try:
        resp = requests.get(API_BASE, params=params, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        adrule_id = data.get("AdmRulService", {}).get("행정규칙기본정보", {}).get("행정규칙ID")
        return str(adrule_id) if adrule_id else None
    except Exception as e:
        logger.warning(f"행정규칙ID 조회 실패 (ID={doc_id}): {e}")
        return None
