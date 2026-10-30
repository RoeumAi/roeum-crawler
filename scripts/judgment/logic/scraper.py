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
from scripts.utils import nlrc_detail
from scripts.utils.nlrc_pdf import extract_attachment_text, preserve_existing_after_failure
from scripts.utils.reference_sub_title import (
    extract_nlrc_case_number,
    extract_nlrc_case_number_from_body,
)
from scripts.core.database.source_versioning import enrich_source_document, sha256_content

logger = get_logger(__name__, scraper_type='judgment')

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do"
}


def clean_text(text: str) -> str:
    return nlrc_detail.clean_text(text)


def _fetch_detail_html(url: str) -> str:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=30, impersonate="chrome110")
    response.raise_for_status()
    return response.text


def _extract_table_value(soup: BeautifulSoup, label: str) -> str:
    return nlrc_detail.extract_table_value(soup, label)


def _parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one(".BD_table th.title")
    title = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""

    data_category = _extract_table_value(soup, "자료구분")
    judgment_points = _extract_table_value(soup, "판정사항")
    judgment_summary = _extract_table_value(soup, "판정요지")
    registered_at = _extract_table_value(soup, "등록일")

    return {
        "title": title,
        "data_category": data_category,
        "judgment_points": judgment_points,
        "judgment_summary": judgment_summary,
        "registered_at": registered_at
    }


def save_to_file(data, filename):
    nlrc_detail.save_to_file(data, filename, logger)


def save_judgment_to_mongodb(document_data: dict) -> bool:
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        collection = db['judgment']

        doc_id = document_data.get("doc_id")
        content_hash = sha256_content(document_data.get("content"))
        existing = collection.find_one(
            {"doc_id": doc_id}, {"content_hash": 1, "metadata.pdf_retry_count": 1}
        )
        if preserve_existing_after_failure(collection, existing, document_data):
            return True

        if existing is None:
            action = "new_version"
        elif existing.get("content_hash") == content_hash:
            action = "no_change"
        else:
            action = "update_existing"
        document_data["_action"] = action

        nlrc_detail.apply_pdf_retry_count(document_data, existing, logger)

        to_store = {k: v for k, v in document_data.items() if k != "_action"}
        to_store = enrich_source_document(to_store, "judgment")
        result = collection.update_one(
            {"doc_id": to_store.get("doc_id")},
            {"$set": to_store},
            upsert=True
        )

        if result.upserted_id:
            logger.info(f"✅ 새 문서 생성: {to_store.get('doc_id')}")
        elif result.modified_count > 0:
            logger.info(f"🔄 문서 업데이트: {to_store.get('doc_id')}")
        else:
            logger.info(f"⏭️  문서 변경 없음: {to_store.get('doc_id')}")

        return True
    except Exception as e:
        logger.error(f"MongoDB 저장 실패: {str(e)}")
        return False


async def scrape_and_save(url: str | dict, output_dir: str, output_name: str, dept_code: str = None,
                          save_to_db: bool = True, save_jsonl: bool = True):
    """
    주요판정사례 상세 페이지를 스크레이핑하여 저장합니다.
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
        html = await asyncio.to_thread(_fetch_detail_html, detail_url)
        parsed = _parse_detail(html)
        
        parsed_url = urlparse(detail_url)
        query_params = parse_qs(parsed_url.query)
        doc_id = query_params.get("jgmtSn", [None])[0] or parsed.get("title")

        html_content = "\n\n".join([
            f"판정사항: {parsed.get('judgment_points', '')}",
            f"판정요지: {parsed.get('judgment_summary', '')}"
        ]).strip()
        pdf_result = await asyncio.to_thread(
            extract_attachment_text,
            html,
            detail_url,
        )
        content = nlrc_detail.merge_attachment_content(html_content, pdf_result, doc_id, logger)

        document_data = nlrc_detail.build_document(
            collection="judgment",
            doc_id=doc_id,
            doc_type="주요판정사례",
            title=parsed.get("title"),
            # 제목에 사건번호가 없는 문서(구세대 312/411)가 다수라, 첨부 판정문
            # 본문에서 한 번 더 추출한다. 상세 페이지에는 사건번호 필드가 없어
            # (2026-08-19 실측) 본문이 유일한 폴백이다. sub_title 이 비면 FE 출처
            # 라벨이 제목 문장으로 나가는 문제의 재발 방지.
            sub_title=(
                extract_nlrc_case_number(parsed.get("title") or "")
                or extract_nlrc_case_number_from_body(content)
            ),
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
            success = await asyncio.to_thread(save_judgment_to_mongodb, document_data)
            if not success:
                return {"doc_id": doc_id, "status": "failed"}

        action = document_data.pop("_action", "new_version")
        return {"doc_id": doc_id, "status": "success", "action": action}

    except Exception as e:
        logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
        return {"doc_id": None, "status": "failed", "error": f"{type(e).__name__}: {e}"}
