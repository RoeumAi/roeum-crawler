"""Shared extraction client with separate text availability and quality status.

파서(chat_generation)는 운영 AI 채팅과 같은 서버다. 10-29 00시 일일 실행이 별표 519개를 쉬지 않고 보내다
그 서버가 메모리 상한에 걸려 재시작했다. 그래서 이 프로세스의 모든 추출 호출은 한 번에 하나씩·간격을 두고
보내고, 서버가 응답하지 않으면(연결 실패·시간 초과·5xx) 잠시 보내지 않는다(_ExtractGate).
"""
import os
import threading
import time
from pathlib import Path
from typing import Callable

import requests as http_requests

from scripts.utils.document_blocks import blocks_markdown, has_table

DEFAULT_CHAT_GENERATION_URL = "http://127.0.0.1:8000"
CONTENT_TYPES = {".pdf": "application/pdf", ".hwp": "application/x-hwp", ".hwpx": "application/vnd.hancom.hwpx"}

# 추출 호출 사이 최소 간격(초)과, 서버가 응답하지 않은 뒤 다시 보내지 않을 시간(초)
EXTRACT_INTERVAL_SECONDS = float(os.getenv("EXTRACT_INTERVAL_SECONDS", "2"))
EXTRACT_PAUSE_SECONDS = float(os.getenv("EXTRACT_PAUSE_SECONDS", "600"))


class ExtractServerUnavailable(RuntimeError):
    """추출 서버가 방금 응답하지 않아 이번 파일을 보내지 않았다."""


class _ExtractGate:
    """스레드 전체에서 추출 호출을 하나씩·간격을 두고 보내고, 서버가 응답하지 않으면 잠시 멈춘다."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self._lock = threading.Lock()
        self._clock, self._sleep = clock, sleep
        self._last_call = None
        self._paused_until = 0.0

    def call(self, fn: Callable[[], dict]) -> dict:
        with self._lock:
            now = self._clock()
            if now < self._paused_until:
                raise ExtractServerUnavailable("추출 서버(chat_generation) 응답 없음 — 잠시 보내지 않음")
            if self._last_call is not None:
                wait = EXTRACT_INTERVAL_SECONDS - (now - self._last_call)
                if wait > 0:
                    self._sleep(wait)
            try:
                return fn()
            except http_requests.RequestException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is None or status >= 500:  # 4xx 는 파일 문제라 멈추지 않는다
                    self._paused_until = self._clock() + EXTRACT_PAUSE_SECONDS
                raise
            finally:
                self._last_call = self._clock()


_gate = _ExtractGate()


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
    return _gate.call(lambda: _post_extract_text(service_url, pdf_bytes, filename))


def _post_extract_text(service_url: str, pdf_bytes: bytes, filename: str) -> dict:
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
    blocks = extracted.get("document_blocks")
    has_structure = isinstance(structured, str) and bool(structured.strip()) and bool(blocks)
    if not verified:
        # The parser already flattens rejected page layouts. Other, valid pages
        # retain their paragraphs/tables even when the document is partial.
        projection["status"] = "needs_review"
    metadata["paragraph_projection"] = projection
    if has_structure and has_table(blocks):
        # 표는 탭 정렬 글 대신 Markdown 표로(화면·검색 조각에서 표가 한 문장으로 뭉개지지 않게).
        text = blocks_markdown(blocks) or text
    return dict(extracted, is_success=True, structured_text=text,
                document_blocks=blocks if has_structure else [{"kind": "text", "text": text}],
                extraction_metadata=metadata, quality_complete=verified)
