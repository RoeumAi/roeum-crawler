"""Shared document extractor client and PDF publication gate."""
import os
from pathlib import Path
import requests as http_requests

DEFAULT_CHAT_GENERATION_URL = "http://127.0.0.1:8000"
CONTENT_TYPES = {".pdf": "application/pdf", ".hwp": "application/x-hwp", ".hwpx": "application/vnd.hancom.hwpx"}

def call_extract_text(
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


def require_complete_pdf(extracted: dict) -> None:
    projection = (extracted.get("extraction_metadata") or {}).get("paragraph_projection") or {}
    if (not extracted.get("is_success") or extracted.get("extraction_status") != "complete"
            or projection.get("status") != "complete" or not extracted.get("document_blocks")
            or not extracted.get("structured_text", "").strip()):
        raise ValueError("PDF page/paragraph coverage is not complete")
