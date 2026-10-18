"""Download NLRC document attachments and extract text via chat_generation."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from scripts.utils.document_extraction import call_extract_text as _call_extract_text, usable_document


NLRC_BASE_URL = "https://nlrc.go.kr"
DOWNLOAD_PATH = "/nlrc/cmmn/file/download.do"
SUPPORTED_EXTENSIONS = {".pdf", ".hwp", ".hwpx"}


def pdf_retry_needed(result: dict) -> bool:
    """Return whether an attached PDF failed extraction and should be retried."""
    if "retry_needed" in result:
        return bool(result["retry_needed"])
    return bool(result.get("attachment")) and not bool(result.get("success"))


def attachment_display_metadata(summary: str, result: dict) -> dict:
    """Pass validated source structure through Mongo/API metadata without cleanup."""
    if not result.get('success') or not result.get('document_blocks'):
        return {}
    blocks = [{'kind': 'text', 'text': part} for part in summary.split('\n\n') if part]
    blocks.append({'kind': 'text', 'role': 'heading', 'text': attachment_body_heading(result)})
    blocks.extend(result['document_blocks'])
    extractions = result.get('extractions', [])
    complete = not result.get('retry_needed') and bool(extractions) and all(
        item.get('metadata', {}).get('paragraph_projection', {}).get('status') == 'complete'
        for item in extractions)
    return {'document_blocks': blocks,
            'paragraph_projection': {'version': 'legal-paragraphs-v1',
                                     'status': 'complete' if complete else 'needs_review'},
            'attachment_extractions': extractions}


def attachment_body_heading(result: dict) -> str:
    return '[첨부파일 추출 본문 · 일부 미확인]' if result.get('retry_needed') else '[첨부파일 전문]'


def preserve_existing_after_failure(collection, existing: dict, document: dict) -> bool:
    """A failed refresh only records retry diagnostics, never replaces old body/hash."""
    metadata = document['metadata']
    if not existing or not metadata.get('pdf_retry_needed'):
        return False
    count = existing.get('metadata', {}).get('pdf_retry_count', 0) + 1
    selector = {'_id': existing['_id']} if '_id' in existing else {'doc_id': document['doc_id']}
    collection.update_one(selector, {'$set': {
        'metadata.pdf_retry_needed': True,
        'metadata.pdf_retry_count': count,
        'metadata.pdf_error': metadata.get('pdf_error', ''),
        'metadata.pdf_last_attempt_status': metadata.get('pdf_extraction_status') or 'failed',
        'metadata.pdf_last_attempt_at': datetime.now().isoformat(),
    }})
    document['_action'] = 'no_change'
    return True


def extract_pdf_attachments(html: str) -> list[dict]:
    """Return supported attachment identifiers embedded in an NLRC detail page.

    The legacy function name is retained for compatibility with existing callers.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    attachments = []

    for anchor in soup.select(
        "a[data-nlrc-event='click-download'][data-file-id][data-key]"
    ):
        name = (anchor.get("title") or anchor.get_text(" ", strip=True)).strip()
        if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        file_id = (anchor.get("data-file-id") or "").strip()
        key = (anchor.get("data-key") or "").strip()
        classification_code = (anchor.get("data-cl-cd") or "").strip()
        if not file_id or not key:
            continue

        query = urlencode(
            {
                "fileCors": "",
                "lgcfNm": "",
                "key": key,
                "fileId": file_id,
            }
        )
        attachments.append(
            {
                "name": name,
                "file_id": file_id,
                "key": key,
                "classification_code": classification_code,
                "download_url": f"{NLRC_BASE_URL}{DOWNLOAD_PATH}?{query}",
            }
        )

    return attachments


def _download_pdf(attachment: dict, detail_url: str) -> bytes:
    response = curl_requests.get(
        attachment["download_url"],
        headers={"Referer": detail_url, "User-Agent": "Mozilla/5.0"},
        timeout=120,
        impersonate="chrome110",
    )
    response.raise_for_status()
    content = response.content
    extension = Path(attachment["name"]).suffix.lower()
    expected_signature = {
        ".pdf": b"%PDF",
        ".hwp": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        ".hwpx": b"PK",
    }[extension]
    if not content.startswith(expected_signature):
        raise RuntimeError(
            f"NLRC attachment response is not a valid {extension} file"
        )
    return content



def extract_attachment_text(
    html: str,
    detail_url: str,
    base_url: str | None = None,
) -> dict:
    """Extract every supported attachment, returning a structured result."""
    attachments = extract_pdf_attachments(html)
    if not attachments:
        return {
            "success": False,
            "text": "",
            "content_source": "html_fallback",
            "attachment": None,
            "attachments": [],
            "page_count": None,
            "is_searchable": None,
            "cost_usd": 0.0,
            "error": "Supported attachment not found",
            "retry_needed": False,
        }

    extracted_parts = []
    content_sources = []
    page_counts = []
    searchable_states = []
    total_cost = 0.0
    errors = []
    document_blocks = []
    extraction_records = []

    for attachment in attachments:
        try:
            file_bytes = _download_pdf(attachment, detail_url)
            extracted = _call_extract_text(
                file_bytes,
                attachment["name"],
                base_url=base_url,
            )
            total_cost += float(extracted.get("cost_usd") or 0.0)
            extracted = usable_document(extracted)
            metadata = extracted['extraction_metadata']
            if not extracted['quality_complete']:
                errors.append(f"{attachment['name']}: extraction_quality_needs_review")
            # The layout projection contains paragraph/table boundaries. Raw
            # full_text retains physical print lines and is a legacy fallback.
            text = (extracted.get("structured_text") or extracted.get("full_text") or "").strip()
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            if not extracted.get("is_success") or not text:
                raise RuntimeError(
                    text or "chat_generation returned no attachment text"
                )

            is_searchable = bool(extracted.get("is_searchable"))
            file_type = str(extracted.get("file_type") or "").lower()
            if file_type in {"hwp", "hwpx"}:
                source = f"{file_type}_text"
            elif file_type in {"hwp_ocr", "hwpx_ocr"}:
                source = file_type
            else:
                source = "pdf_text" if is_searchable else "pdf_ocr"

            extracted_parts.append(f"[첨부: {attachment['name']}]\n\n{text}")
            document_blocks.append({"kind": "text", "role": "heading", "text": f"[첨부: {attachment['name']}]"})
            document_blocks.extend(extracted.get("document_blocks") or [{"kind": "text", "text": text}])
            extraction_records.append({"name": attachment["name"], "metadata": metadata,
                                       "extraction_status": extracted.get("extraction_status")})
            content_sources.append(source)
            if extracted.get("page_count") is not None:
                page_counts.append(int(extracted["page_count"]))
            searchable_states.append(is_searchable)
        except Exception as exc:
            errors.append(f"{attachment['name']}: {exc}")

    # Preserve available attachments and expose incompleteness separately.
    success = bool(extracted_parts)
    return {
        "success": success,
        "text": "\n\n".join(extracted_parts),
        "document_blocks": document_blocks,
        "extractions": extraction_records,
        "extraction_status": "complete" if success and not errors else "partial" if success else "failed",
        "content_source": (
            content_sources[0]
            if len(set(content_sources)) == 1
            else "mixed_attachments"
            if content_sources
            else "html_fallback"
        ),
        "attachment": attachments[0],
        "attachments": attachments,
        "page_count": sum(page_counts) if page_counts else None,
        "is_searchable": (
            all(searchable_states) if searchable_states else None
        ),
        "cost_usd": total_cost,
        "error": "; ".join(errors),
        "retry_needed": bool(errors),
    }
