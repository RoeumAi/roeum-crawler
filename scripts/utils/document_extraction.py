"""Shared extraction client with separate text availability and quality status."""
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


def usable_document(extracted: dict) -> dict:
    """Return available text; completeness is quality metadata, not availability."""
    structured = extracted.get("structured_text")
    raw = extracted.get("full_text")
    text = structured if isinstance(structured, str) and structured.strip() else raw
    if (not isinstance(text, str) or not text.strip() or
            not (extracted.get("is_success") is True or extracted.get("extraction_status") == "partial")):
        raise ValueError("Document extraction returned no usable text")
    metadata = dict(extracted.get("extraction_metadata") or {})
    projection = dict(metadata.get("paragraph_projection") or {})
    verified = (extracted.get("extraction_status") == "complete" and
                projection.get("status") == "complete" and bool(extracted.get("document_blocks")))
    if not verified:
        # Failed structure validation must not discard readable source text or
        # present the rejected layout as a verified table/paragraph structure.
        text = raw if isinstance(raw, str) and raw.strip() else text
        projection["status"] = "needs_review"
    metadata["paragraph_projection"] = projection
    return dict(extracted, is_success=True, structured_text=text,
                document_blocks=extracted["document_blocks"] if verified else [{"kind": "text", "text": text}],
                extraction_metadata=metadata, quality_complete=verified)
