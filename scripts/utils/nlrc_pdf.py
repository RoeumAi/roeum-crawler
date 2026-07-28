"""Download NLRC document attachments and extract text via chat_generation."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlencode

import requests as http_requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests


NLRC_BASE_URL = "https://nlrc.go.kr"
DOWNLOAD_PATH = "/nlrc/cmmn/file/download.do"
DEFAULT_CHAT_GENERATION_URL = "http://127.0.0.1:8000"
SUPPORTED_EXTENSIONS = {".pdf", ".hwp", ".hwpx"}
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".hwp": "application/x-hwp",
    ".hwpx": "application/vnd.hancom.hwpx",
}


def pdf_retry_needed(result: dict) -> bool:
    """Return whether an attached PDF failed extraction and should be retried."""
    if "retry_needed" in result:
        return bool(result["retry_needed"])
    return bool(result.get("attachment")) and not bool(result.get("success"))


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


def _call_extract_text(
    pdf_bytes: bytes,
    filename: str,
    base_url: str | None = None,
) -> dict:
    service_url = (
        base_url
        or os.getenv("CHAT_GENERATION_URL")
        or DEFAULT_CHAT_GENERATION_URL
    ).rstrip("/")
    response = http_requests.post(
        f"{service_url}/api/extract-text",
        files={
            "file": (
                filename,
                pdf_bytes,
                CONTENT_TYPES.get(
                    Path(filename).suffix.lower(),
                    "application/octet-stream",
                ),
            )
        },
        timeout=900,
    )
    response.raise_for_status()
    return response.json()


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

    for attachment in attachments:
        try:
            file_bytes = _download_pdf(attachment, detail_url)
            extracted = _call_extract_text(
                file_bytes,
                attachment["name"],
                base_url=base_url,
            )
            text = (extracted.get("full_text") or "").strip()
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

            extracted_parts.append(f"[첨부: {attachment['name']}]\n{text}")
            content_sources.append(source)
            if extracted.get("page_count") is not None:
                page_counts.append(int(extracted["page_count"]))
            searchable_states.append(is_searchable)
            total_cost += float(extracted.get("cost_usd") or 0.0)
        except Exception as exc:
            errors.append(f"{attachment['name']}: {exc}")

    success = bool(extracted_parts)
    return {
        "success": success,
        "text": "\n\n".join(extracted_parts),
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
