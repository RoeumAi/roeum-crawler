"""
law.go.kr 상세 API로 법령/행정규칙의 안정 ID(법령ID/행정규칙ID)를 조회한다.

같은 법령·행정규칙이라도 개정될 때마다 새 doc_id(MST/일련번호)가 발급되므로,
버전을 그룹핑하려면 이 안정 ID가 필요하다.
"""

from typing import Optional

from scripts.core.lawgokr_openapi import SERVICE_URL, OpenApiError, request_json
from scripts.core.run_report import call_with_retry
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='lawgokr_stable_id')

TIMEOUT_SECONDS = 10


def fetch_record(target: str, doc_id: str) -> Optional[dict]:
    """Open API 상세(JSON). target=law 는 MST, admrul 은 ID(일련번호). 실패 시 None.

    멀쩡한 문서에도 잠깐 404 를 줄 때가 있어(10-29 법령 2건) 몇 번 더 시도한다."""
    key = "MST" if target == "law" else "ID"
    params = {"target": target, key: doc_id}
    try:
        # 요청·오류 판정은 Open API 공통(lawgokr_openapi.request_json). 응답 전체를 돌려준다(호출부가 최상위 키를 본다).
        # 잠깐 나는 404·네트워크 오류만 다시 시도한다. '없는 문서' 안내(OpenApiError)는 다시 해도 같다.
        return call_with_retry(lambda: request_json(SERVICE_URL, params, timeout=TIMEOUT_SECONDS),
                               retry_if=lambda exc: not isinstance(exc, OpenApiError))
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


def iso_date(value) -> Optional[str]:
    """'20250805' → '2025-08-05'. 형식이 다르면 None(추측하지 않는다)."""
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return None


def adrule_basic_meta(record: Optional[dict]) -> dict:
    """행정규칙 상세 API 기본정보에서 인용·버전 판단에 쓰는 값만 꺼낸다(없는 값은 넣지 않는다).

    - promulgation_date: 발령일자(고시일, YYYY-MM-DD)
    - promulgation_number: 발령번호(예: 2025-47)
    - dept_name: 소관부처명(예: 고용노동부)
    - revision_kind: 제개정구분명(예: 제정·일부개정·폐지)
    - rule_kind: 행정규칙종류(예: 고시·훈령·예규)
    """
    basic = ((record or {}).get("AdmRulService") or {}).get("행정규칙기본정보") or {}
    meta = {
        "promulgation_date": iso_date(basic.get("발령일자")),
        "promulgation_number": str(basic.get("발령번호") or "").strip() or None,
        "dept_name": str(basic.get("소관부처명") or "").strip() or None,
        "revision_kind": str(basic.get("제개정구분명") or "").strip() or None,
        "rule_kind": str(basic.get("행정규칙종류") or "").strip() or None,
    }
    return {k: v for k, v in meta.items() if v}
