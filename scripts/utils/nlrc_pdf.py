"""Download NLRC PDF attachments and extract their text via chat_generation."""

from __future__ import annotations

import os
from urllib.parse import urlencode

import requests as http_requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests


NLRC_BASE_URL = "https://nlrc.go.kr"
DOWNLOAD_PATH = "/nlrc/cmmn/file/download.do"
DEFAULT_CHAT_GENERATION_URL = "http://127.0.0.1:8000"


def pdf_retry_needed(result: dict) -> bool:
    """Return whether an attached PDF failed extraction and should be retried."""
    return bool(result.get("attachment")) and not bool(result.get("success"))


def extract_pdf_attachments(html: str) -> list[dict]:
    """Return PDF attachment identifiers embedded in an NLRC detail page."""
    soup = BeautifulSoup(html or "", "html.parser")
    attachments = []

    for anchor in soup.select(
        "a[data-nlrc-event='click-download'][data-file-id][data-key]"
    ):
        name = (anchor.get("title") or anchor.get_text(" ", strip=True)).strip()
        if not name.lower().endswith(".pdf"):
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
    if not content.startswith(b"%PDF"):
        raise RuntimeError("NLRC attachment response is not a PDF")
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
        files={"file": (filename, pdf_bytes, "application/pdf")},
        timeout=900,
    )
    response.raise_for_status()
    return response.json()


def extract_attachment_text(
    html: str,
    detail_url: str,
    base_url: str | None = None,
) -> dict:
    """Extract the first PDF attachment, returning a structured fallback result."""
    attachments = extract_pdf_attachments(html)
    if not attachments:
        return {
            "success": False,
            "text": "",
            "content_source": "html_fallback",
            "attachment": None,
            "page_count": None,
            "is_searchable": None,
            "cost_usd": 0.0,
            "error": "PDF attachment not found",
        }

    attachment = attachments[0]
    try:
        pdf_bytes = _download_pdf(attachment, detail_url)
        extracted = _call_extract_text(
            pdf_bytes,
            attachment["name"],
            base_url=base_url,
        )
        text = (extracted.get("full_text") or "").strip()
        if not extracted.get("is_success") or not text:
            raise RuntimeError(text or "chat_generation returned no PDF text")

        is_searchable = bool(extracted.get("is_searchable"))
        return {
            "success": True,
            "text": text,
            "content_source": "pdf_text" if is_searchable else "pdf_ocr",
            "attachment": attachment,
            "page_count": extracted.get("page_count"),
            "is_searchable": is_searchable,
            "cost_usd": float(extracted.get("cost_usd") or 0.0),
            "error": "",
        }
    except Exception as exc:
        return {
            "success": False,
            "text": "",
            "content_source": "html_fallback",
            "attachment": attachment,
            "page_count": None,
            "is_searchable": None,
            "cost_usd": 0.0,
            "error": str(exc),
        }
