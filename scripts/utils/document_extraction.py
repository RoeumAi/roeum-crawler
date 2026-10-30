"""Shared extraction client with separate text availability and quality status.

파서(chat_generation)는 운영 AI 채팅과 같은 서버다. 10-29·10-30 00시 일일 실행에서 별표 PDF 를 읽다
그 서버가 메모리 상한에 걸려 재시작했다. 그래서 이 프로세스의 모든 추출 호출은 한 번에 하나씩·간격을 두고
보낸다(_ExtractGate). 서버가 응답하지 않으면(연결 실패·시간 초과·5xx) 그 파일은 실패로 두고, 다음 호출 전에
서버가 다시 살아날 때까지(/health) 기다렸다가 이어 보낸다. 예전에는 10분 동안 무조건 보내지 않아, 1분 만에
돌아온 서버를 두고 남은 별표 전부가 실패로 남았다(10-30 행정규칙 49건).
같은 실행에서 서버가 여러 번 멈추면 사용자 채팅을 지키려고 그 실행에서는 더 보내지 않는다.
"""
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests as http_requests

from scripts.utils.document_blocks import blocks_markdown, has_table

DEFAULT_CHAT_GENERATION_URL = "http://127.0.0.1:8000"
CONTENT_TYPES = {".pdf": "application/pdf", ".hwp": "application/x-hwp", ".hwpx": "application/vnd.hancom.hwpx"}

# 추출 호출 사이 최소 간격(초)
EXTRACT_INTERVAL_SECONDS = float(os.getenv("EXTRACT_INTERVAL_SECONDS", "2"))
# 서버가 응답하지 않은 뒤 되살아나기를 기다리는 최대 시간(초)과 /health 확인 간격(초)
EXTRACT_RECOVERY_WAIT_SECONDS = float(os.getenv("EXTRACT_RECOVERY_WAIT_SECONDS", "600"))
EXTRACT_HEALTH_POLL_SECONDS = float(os.getenv("EXTRACT_HEALTH_POLL_SECONDS", "15"))
# 한 실행에서 서버가 이만큼 멈추면 그 실행에서는 더 보내지 않는다
EXTRACT_MAX_SERVER_FAILURES = int(os.getenv("EXTRACT_MAX_SERVER_FAILURES", "2"))


class ExtractServerUnavailable(RuntimeError):
    """추출 서버가 응답하지 않아 이번 파일을 보내지 않았다."""


def _is_server_failure(error: http_requests.RequestException) -> bool:
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status is None or status >= 500  # 4xx 는 파일 문제라 서버 탓이 아니다


def _server_healthy(service_url: str) -> bool:
    try:
        return http_requests.get(f"{service_url}/health", timeout=5).status_code == 200
    except http_requests.RequestException:
        return False


class _ExtractGate:
    """스레드 전체에서 추출 호출을 하나씩·간격을 두고 보내고, 서버가 멈추면 되살아날 때까지 기다린다."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep, healthy: Callable[[str], bool] = _server_healthy):
        self._lock = threading.Lock()
        self._clock, self._sleep, self._healthy = clock, sleep, healthy
        self._last_call = None
        self._server_failures = 0
        self._recovering = False
        self._given_up: Optional[str] = None

    def call(self, fn: Callable[[], dict], service_url: str = "") -> dict:
        with self._lock:
            if self._given_up:
                raise ExtractServerUnavailable(self._given_up)
            if self._recovering:
                self._wait_for_recovery(service_url)
            if self._last_call is not None:
                wait = EXTRACT_INTERVAL_SECONDS - (self._clock() - self._last_call)
                if wait > 0:
                    self._sleep(wait)
            try:
                return fn()
            except http_requests.RequestException as e:
                if _is_server_failure(e):
                    self._server_failures += 1
                    if self._server_failures >= EXTRACT_MAX_SERVER_FAILURES:
                        self._given_up = (f"추출 서버(chat_generation)가 이번 실행에서 {self._server_failures}번 멈춰 "
                                          "더 보내지 않음")
                    else:
                        self._recovering = True
                raise
            finally:
                self._last_call = self._clock()

    def _wait_for_recovery(self, service_url: str) -> None:
        deadline = self._clock() + EXTRACT_RECOVERY_WAIT_SECONDS
        while not self._healthy(service_url):
            if self._clock() >= deadline:
                self._given_up = (f"추출 서버(chat_generation) 응답 없음 — {EXTRACT_RECOVERY_WAIT_SECONDS:.0f}초 "
                                  "기다려도 돌아오지 않아 더 보내지 않음")
                raise ExtractServerUnavailable(self._given_up)
            self._sleep(EXTRACT_HEALTH_POLL_SECONDS)
        self._recovering = False


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
    return _gate.call(lambda: _post_extract_text(service_url, pdf_bytes, filename), service_url)


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
