"""중앙노동위원회 상세 수집(주요판정사례·조정사건례)이 같이 쓰는 조각.

두 컬렉션은 가져오는 방식(GET/POST)·파싱 필드·사건번호 추출·저장 방식이 달라 각자의 scraper.py 에 두고,
글자 정리·표 값 읽기·첨부 본문 합치기·문서 메타데이터 구성·재시도 횟수 계산·JSONL 저장처럼 똑같은 조각만 여기 둔다.
"""
import json
import os
from datetime import datetime
from html import unescape

from bs4 import BeautifulSoup

from scripts.core.identifiers import document_chunk_id
from scripts.utils.nlrc_pdf import attachment_body_heading, attachment_display_metadata, pdf_retry_needed

MAX_PDF_RETRIES = 3


def clean_text(text: str) -> str:
    if not text:
        return ""
    return "\n".join(" ".join(line.split()) for line in unescape(text).splitlines()).strip()


def extract_table_value(soup: BeautifulSoup, label: str) -> str:
    """상세 표에서 라벨(th) 옆 칸(td)의 값을 읽는다."""
    th = soup.find("th", string=lambda s: s and s.strip() == label)
    if not th:
        return ""
    td = th.find_next_sibling("td")
    if not td:
        return ""
    return clean_text(td.get_text(separator="\n"))


def save_to_file(data, filename, log):
    if not isinstance(data, list):
        data = [data]
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    log.info(f"데이터 {len(data)}건을 '{filename}' 파일에 성공적으로 저장했습니다.")


def merge_attachment_content(html_content: str, pdf_result: dict, doc_id: str, log) -> str:
    """HTML 요약 뒤에 첨부파일 본문을 붙인다. 추출 실패면 HTML 요약만 둔다."""
    if pdf_result["success"]:
        content = f"{html_content}\n\n{attachment_body_heading(pdf_result)}\n{pdf_result['text']}".strip()
        log.info(
            f"✅ {doc_id}: 첨부파일 본문 추출 완료 "
            f"({pdf_result['content_source']}, {len(pdf_result['text']):,}자)"
        )
        return content
    log.warning(
        f"⚠️ {doc_id}: 첨부파일 본문 추출 실패 — HTML 요약 유지 "
        f"({pdf_result['error']})"
    )
    return html_content


def build_document(*, collection: str, doc_id: str, doc_type: str, title: str, sub_title: str,
                   content: str, html_content: str, pdf_result: dict, detail_url: str,
                   registered_at: str) -> dict:
    attachment = pdf_result.get("attachment") or {}
    return {
        "chunk_id": document_chunk_id(collection, doc_id),
        "doc_id": doc_id,
        "doc_type": doc_type,
        "article_number": "1",
        "title": title,
        "sub_title": sub_title,
        "content": content,
        "metadata": {
            **attachment_display_metadata(html_content, pdf_result),
            "source_url": detail_url,
            "source_type": "web",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "is_active": True,
            "effective": registered_at,
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
            "pdf_extraction_status": pdf_result.get("extraction_status"),
            "pdf_is_searchable": pdf_result.get("is_searchable"),
            "pdf_cost_usd": pdf_result.get("cost_usd", 0.0),
            "pdf_error": pdf_result.get("error", ""),
            "pdf_retry_needed": pdf_retry_needed(pdf_result),
        },
    }


def apply_pdf_retry_count(document_data: dict, existing: dict | None, log) -> None:
    """첨부 추출 실패가 이어진 횟수를 세어 metadata 에 적는다. 상한에 닿으면 재시도를 멈춘다."""
    metadata = document_data["metadata"]
    if metadata.get("pdf_retry_needed"):
        prev_retry_count = (existing or {}).get("metadata", {}).get("pdf_retry_count", 0)
        retry_count = prev_retry_count + 1
        if retry_count >= MAX_PDF_RETRIES:
            log.warning(
                f"⚠️ {document_data.get('doc_id')}: 첨부파일 추출 {MAX_PDF_RETRIES}회 연속 실패 — "
                f"재시도 중단, 확보한 본문 유지"
            )
            metadata["pdf_retry_needed"] = False
        metadata["pdf_retry_count"] = retry_count
    else:
        metadata["pdf_retry_count"] = 0
