import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from curl_cffi import requests
from bs4 import BeautifulSoup

# 프로젝트 루트 경로 설정
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.utils.nlrc_pdf import extract_attachment_text, pdf_retry_needed
from scripts.core.identifiers import document_chunk_id
from scripts.utils.reference_sub_title import extract_nlrc_case_number
from scripts.core.database.source_versioning import enrich_source_document

logger = get_logger(__name__, scraper_type='judgment')

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do"
}


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


def _fetch_detail_html(url: str) -> str:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=30, impersonate="chrome110")
    response.raise_for_status()
    return response.text


def _extract_table_value(soup: BeautifulSoup, label: str) -> str:
    th = soup.find("th", string=lambda s: s and s.strip() == label)
    if not th:
        return ""
    td = th.find_next_sibling("td")
    if not td:
        return ""
    return clean_text(td.get_text(separator="\n"))


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
    if not isinstance(data, list):
        data = [data]
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    logger.info(f"데이터 {len(data)}건을 '{filename}' 파일에 성공적으로 저장했습니다.")


def save_judgment_to_mongodb(document_data: dict) -> bool:
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        collection = db['judgment']

        document_data = enrich_source_document(document_data, "judgment")
        result = collection.update_one(
            {"doc_id": document_data.get("doc_id")},
            {"$set": document_data},
            upsert=True
        )

        if result.upserted_id:
            logger.info(f"✅ 새 문서 생성: {document_data.get('doc_id')}")
        elif result.modified_count > 0:
            logger.info(f"🔄 문서 업데이트: {document_data.get('doc_id')}")
        else:
            logger.info(f"⏭️  문서 변경 없음: {document_data.get('doc_id')}")

        return True
    except Exception as e:
        logger.error(f"MongoDB 저장 실패: {str(e)}")
        return False


async def scrape_and_save(url: str | dict, output_dir: str, output_name: str, dept_code: str = None,
                          save_to_db: bool = True, save_jsonl: bool = True):
    """
    주요판정사례 상세 페이지를 스크레이핑하여 저장합니다.
    unified_scraper_flow에서 호출하는 표준 함수입니다.
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
        if pdf_result["success"]:
            content = f"{html_content}\n\n[첨부파일 전문]\n{pdf_result['text']}".strip()
            logger.info(
                f"✅ {doc_id}: 첨부파일 본문 추출 완료 "
                f"({pdf_result['content_source']}, {len(pdf_result['text']):,}자)"
            )
        else:
            content = html_content
            logger.warning(
                f"⚠️ {doc_id}: 첨부파일 본문 추출 실패 — HTML 요약 유지 "
                f"({pdf_result['error']})"
            )

        attachment = pdf_result.get("attachment") or {}

        document_data = {
            "chunk_id": document_chunk_id("judgment", doc_id),
            "doc_id": doc_id,
            "doc_type": "주요판정사례",
            "article_number": "1",
            "title": parsed.get("title"),
            "sub_title": extract_nlrc_case_number(parsed.get("title") or ""),
            "content": content,
            "metadata": {
                "source_url": detail_url,
                "source_type": "web",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "is_active": True,
                "effective": parsed.get("registered_at"),
                "attachment_name": attachment.get("name"),
                "attachment_url": attachment.get("download_url"),
                "attachment_file_id": attachment.get("file_id"),
                "attachment_names": [
                    item.get("name") for item in pdf_result.get("attachments", [])
                ],
                "attachment_file_ids": [
                    item.get("file_id") for item in pdf_result.get("attachments", [])
                ],
                "content_source": pdf_result.get("content_source"),
                "pdf_page_count": pdf_result.get("page_count"),
                "pdf_is_searchable": pdf_result.get("is_searchable"),
                "pdf_cost_usd": pdf_result.get("cost_usd", 0.0),
                "pdf_error": pdf_result.get("error", ""),
                "pdf_retry_needed": pdf_retry_needed(pdf_result),
            }
        }

        if save_jsonl:
            output_path = os.path.join(output_dir, f"{output_name}_document.jsonl")
            save_to_file(document_data, output_path)

        if save_to_db:
            success = await asyncio.to_thread(save_judgment_to_mongodb, document_data)
            if not success:
                return {"doc_id": doc_id, "status": "failed"}

        return {"doc_id": doc_id, "status": "success"}

    except Exception as e:
        logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
        return {"doc_id": None, "status": "failed"}
