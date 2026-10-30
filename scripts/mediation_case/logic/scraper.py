import asyncio
import os
import sys
from urllib.parse import urlparse, parse_qs

from curl_cffi import requests
from bs4 import BeautifulSoup

# 프로젝트 루트 경로 설정
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.run_report import retry_detail_fetch_async
from scripts.utils import nlrc_detail
from scripts.utils.nlrc_pdf import extract_attachment_text, preserve_existing_after_failure
from scripts.utils.reference_sub_title import extract_nlrc_case_number

logger = get_logger(__name__, scraper_type='mediation_case')

DETAIL_URL = "https://nlrc.go.kr/nlrc/mainCase/mediatioin/detail.do"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do",
    "Content-Type": "application/x-www-form-urlencoded"
}


def clean_text(text: str) -> str:
    """텍스트 정리"""
    return nlrc_detail.clean_text(text)


def _fetch_detail_html(jgmt_sn: str, jgmt_dcsn_se_cd: str = "66") -> str:
    """상세 페이지 HTML 가져오기 - POST 요청"""
    data = {
        'jgmtSn': jgmt_sn,
        'jgmtDcsnSeCd': jgmt_dcsn_se_cd,
        'event': 'click-detail'
    }
    response = requests.post(DETAIL_URL, data=data, headers=DEFAULT_HEADERS, timeout=30, impersonate="chrome110")
    response.raise_for_status()
    return response.text


def _extract_table_value(soup: BeautifulSoup, label: str) -> str:
    """테이블에서 특정 라벨의 값 추출"""
    return nlrc_detail.extract_table_value(soup, label)


def _parse_detail(html: str) -> dict:
    """상세 페이지 파싱"""
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one(".BD_table th.title")
    title = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""

    data_category = _extract_table_value(soup, "자료구분")
    summary = _extract_table_value(soup, "요약내용")  # 요약내용만 추출
    registered_at = _extract_table_value(soup, "등록일")

    return {
        "title": title,
        "data_category": data_category,
        "summary": summary,
        "registered_at": registered_at
    }


def save_to_file(data, filename):
    """JSONL 파일로 저장"""
    nlrc_detail.save_to_file(data, filename, logger)


def save_mediation_to_mongodb(document_data: dict) -> bool:
    """변경 감지 포함 MongoDB 저장"""
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        from scripts.core.database.unified_repository import UnifiedDocumentRepository

        db = get_mongo_db()
        repo = UnifiedDocumentRepository(db, collection_name='mediation_case')

        doc_id = document_data.get("doc_id")
        existing = None
        if document_data["metadata"].get("pdf_retry_needed"):
            existing = repo.find_by_id(doc_id, active_only=True)
            if preserve_existing_after_failure(db['mediation_case'], existing, document_data):
                return True
        nlrc_detail.apply_pdf_retry_count(document_data, existing, logger)

        doc_id, action_result = repo.upsert_with_change_detection(document_data)
        action = action_result.get("action", "unknown")
        version = action_result.get("version", 1)

        if action == "insert":
            logger.info(f"💾 신규 저장: {doc_id} (v{version})")
        elif action == "new_version":
            changed = ', '.join(action_result.get('changed_fields', []))
            logger.info(f"✅ 새 버전: {doc_id} (v{version}) - 변경: {changed or 'metadata'}")
        elif action == "update_existing":
            logger.info(f"🔄 유지: {doc_id} (v{version}) - metadata만 업데이트")

        return True
    except Exception as e:
        logger.error(f"MongoDB 저장 실패: {str(e)}")
        return False


async def scrape_and_save(url: str | dict, output_dir: str, output_name: str, dept_code: str = None,
                          save_to_db: bool = True, save_jsonl: bool = True):
    """
    조정사건례 상세 페이지를 스크레이핑하여 저장합니다.
    crawl.py(run_single_scraper)에서 호출하는 표준 함수입니다.
    """
    if isinstance(url, dict):
        detail_url = url.get("url") or ""
    else:
        detail_url = url

    if not detail_url:
        logger.error("상세 URL이 비어있습니다.")
        return {"doc_id": None, "status": "failed"}

    logger.info(f"상세 페이지 스크레이핑 시작: {detail_url}")

    try:
        # Parse jgmtSn and jgmtDcsnSeCd from URL
        parsed_url = urlparse(detail_url)
        query_params = parse_qs(parsed_url.query)
        jgmt_sn = query_params.get("jgmtSn", [None])[0]
        jgmt_dcsn_se_cd = query_params.get("jgmtDcsnSeCd", ["66"])[0]
        
        if not jgmt_sn:
            logger.error(f"URL에 jgmtSn 파라미터가 없습니다: {detail_url}")
            return {"doc_id": None, "status": "failed"}
        
        html = await retry_detail_fetch_async(lambda: asyncio.to_thread(_fetch_detail_html, jgmt_sn, jgmt_dcsn_se_cd))
        parsed = _parse_detail(html)
        
        doc_id = jgmt_sn  # 숫자만 사용

        html_content = parsed.get('summary', '')
        pdf_result = await asyncio.to_thread(
            extract_attachment_text,
            html,
            detail_url,
        )
        content = nlrc_detail.merge_attachment_content(html_content, pdf_result, doc_id, logger)

        document_data = nlrc_detail.build_document(
            collection="mediation_case",
            doc_id=doc_id,
            doc_type="조정사건례",
            title=parsed.get("title"),
            sub_title=extract_nlrc_case_number(parsed.get("title") or ""),
            content=content,
            html_content=html_content,
            pdf_result=pdf_result,
            detail_url=detail_url,
            registered_at=parsed.get("registered_at"),
        )

        if save_jsonl:
            output_path = os.path.join(output_dir, f"{output_name}_document.jsonl")
            save_to_file(document_data, output_path)

        if save_to_db:
            success = await asyncio.to_thread(save_mediation_to_mongodb, document_data)
            if not success:
                return {"doc_id": doc_id, "status": "failed"}

        return {"doc_id": doc_id, "status": "success"}

    except Exception as e:
        logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
        return {"doc_id": None, "status": "failed", "error": f"{type(e).__name__}: {e}"}
