"""스크래퍼 한 번 실행 중에 생긴 실패 대상을 모은다.

목록 페이지 하나가 실패해도 스크래퍼는 나머지 페이지로 계속 진행한다. 예전에는
이런 실패가 경고 로그로만 남아 일일 결과에서 보이지 않았다(10-14 법제처해석례
179건 중 137건만 수집). 목록 스크래퍼는 실패를 여기에 기록하고, crawl.py가
스크래퍼 결과의 failed_targets 로 내보낸다.

스크래퍼마다 스레드·이벤트 루프가 따로라 contextvars 로 실행 단위를 나눈다.
"""

import asyncio
import contextvars
import logging
import threading
import time
from collections import Counter
from typing import Awaitable, Callable, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_current: contextvars.ContextVar[Optional["RunReport"]] = contextvars.ContextVar(
    "crawl_run_report", default=None
)


class RunReport:
    def __init__(self, scraper_type: str):
        self.scraper_type = scraper_type
        self.failed_targets: List[str] = []
        self.reasons: List[str] = []
        # 원인 분류별 건수 — 일일 요약의 "실패 원인" 줄. 문서 실패는 원인을 아는 곳(별표 등)에서 note_reason 으로 센다.
        self.reason_counts: Counter = Counter()
        # 상세 페이지 재시도에 쓴 시간(초) — 스크래퍼 한 번 실행의 재시도 총량을 묶는 데 쓴다(call_with_retry 의 budgeted).
        self.retry_seconds_used = 0.0
        self._retry_lock = threading.Lock()

    def fail(self, target: str, reason: str) -> None:
        self.failed_targets.append(f"{self.scraper_type}:{target}")
        self.reasons.append(f"{target}: {reason}"[:300])
        self.reason_counts[classify_failure(reason)] += 1


    def retry_budget_left(self) -> float:
        return DETAIL_RETRY_BUDGET_SECONDS - self.retry_seconds_used

    def charge_retry_time(self, seconds: float) -> None:
        with self._retry_lock:
            self.retry_seconds_used += seconds


def start_run_report(scraper_type: str) -> RunReport:
    """현재 실행 컨텍스트에 새 보고를 건다. 같은 컨텍스트에서 만든 태스크·스레드에도 보인다."""
    report = RunReport(scraper_type)
    _current.set(report)
    return report


def record_failure(target: str, reason) -> None:
    """현재 스크래퍼 실행의 실패 대상을 기록한다. 보고가 없으면(단독 실행) 무시한다."""
    report = _current.get()
    if report is not None:
        report.fail(target, str(reason))


EXTRACT_SERVER_DOWN = "문서 추출 서버(chat_generation) 연결 실패"
LAWGOKR_API_ERROR = "국가법령 Open API 일시 오류"
TIMEOUT = "응답 시간 초과"
OTHER = "기타 오류"


def classify_failure(reason) -> str:
    """실패 메시지를 사람이 읽을 원인 분류로 줄인다(요약에 원인별 건수로 보인다)."""
    text = str(reason)
    if any(k in text for k in ("chat-generation", "chat_generation", "extract-text", "추출 서버")):
        return EXTRACT_SERVER_DOWN
    if "law.go.kr" in text or "Open API" in text:
        return LAWGOKR_API_ERROR
    if "Timeout" in text or "timed out" in text:
        return TIMEOUT
    return OTHER


def note_reason(category: str) -> None:
    """실패 대상은 따로 세는 곳(문서 결과)이 있고 원인만 남길 때 쓴다. 보고가 없으면 무시."""
    report = _current.get()
    if report is not None:
        report.reason_counts[category] += 1


# ============================================================================
# 재시도
# ============================================================================
# 목록 페이지: 5·10초 간격 3회, 어떤 예외든 다시 시도한다(law.go.kr Open API 가 멀쩡한 페이지에도 잠깐 404 를 준다).
# 상세 페이지: 같은 간격·횟수지만 네트워크 오류·시간 초과·5xx 만 다시 시도하고(is_transient_error),
#   문서 하나당 재시도 시간과 스크래퍼 한 번 실행의 재시도 총시간에 상한을 둔다 → 외부 수집원이 길게 죽어 있어도
#   일일 실행이 상한(CRAWLER_TIME_LIMIT, 기본 170분)을 넘기도록 늘어나지 않는다.
DETAIL_RETRY_ATTEMPTS = 3
DETAIL_RETRY_WAIT_SECONDS = 5.0
DETAIL_RETRY_MAX_SECONDS_PER_CALL = 90.0     # 첫 시도 시작부터 이 시간이 지났거나 다음 대기로 넘으면 더 시도하지 않는다
DETAIL_RETRY_BUDGET_SECONDS = 900.0          # 스크래퍼 한 번 실행에서 재시도(대기+재시도 요청)에 쓸 수 있는 총 스레드 시간

_TRANSIENT_TYPE_NAMES = {
    "ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout", "TimeoutError",
    "ChunkedEncodingError", "IncompleteRead",
}
_TRANSIENT_BROWSER_MARKERS = (
    "net::ERR_CONNECTION", "net::ERR_TIMED_OUT", "net::ERR_NETWORK", "net::ERR_NAME_NOT_RESOLVED",
    "net::ERR_EMPTY_RESPONSE", "net::ERR_HTTP2",
)


def is_transient_error(exc: BaseException) -> bool:
    """네트워크 오류·시간 초과·5xx 면 True. 4xx·파싱 오류·코드 오류는 다시 해도 같으므로 False.

    requests·curl_cffi·Playwright 를 직접 import 하지 않고 응답 상태·예외 클래스 이름으로 판단한다.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status >= 500
    if any(cls.__name__ in ("SSLError", "CertificateVerifyError") for cls in type(exc).__mro__):
        return False  # 인증서 오류는 다시 해도 같다
    if any(cls.__name__ in _TRANSIENT_TYPE_NAMES for cls in type(exc).__mro__):
        return True
    return any(marker in str(exc) for marker in _TRANSIENT_BROWSER_MARKERS)


class _RetryState:
    """한 번의 call_with_retry 호출 동안 시간·재시도 총량을 따라간다(동기·비동기 공용)."""

    def __init__(self, attempts, wait_seconds, retry_if, max_total_seconds, budgeted, on_retry, clock):
        self.attempts = attempts
        self.wait_seconds = wait_seconds
        self.retry_if = retry_if
        self.max_total_seconds = max_total_seconds
        self.on_retry = on_retry
        self.clock = clock
        self.report = _current.get() if budgeted else None
        self.started = clock()
        self.attempt_started = self.started
        self.retrying = False
        self.is_retry_attempt = False

    def begin_attempt(self) -> None:
        self.attempt_started = self.clock()
        self.is_retry_attempt = self.retrying

    def end_attempt(self) -> None:
        # 재시도 요청이 걸린 시간도 총량에 센다(첫 시도는 원래 하던 일이라 세지 않는다).
        if self.is_retry_attempt and self.report is not None:
            self.report.charge_retry_time(self.clock() - self.attempt_started)

    def next_wait(self, attempt: int, exc: Exception) -> Optional[float]:
        """다시 시도해야 하면 대기 시간(초), 아니면 None."""
        if attempt >= self.attempts:
            return None
        if self.retry_if is not None and not self.retry_if(exc):
            return None
        wait = self.wait_seconds * attempt
        if self.max_total_seconds is not None and (self.clock() - self.started) + wait >= self.max_total_seconds:
            return None
        if self.report is not None and self.report.retry_budget_left() <= wait:
            return None
        if self.on_retry is not None:
            self.on_retry(attempt, exc)
        if self.report is not None:
            self.report.charge_retry_time(wait)
        self.retrying = True
        return wait


def call_with_retry(fn: Callable[[], T], attempts: int = 3, wait_seconds: float = 5.0, *,
                    retry_if: Optional[Callable[[Exception], bool]] = None,
                    max_total_seconds: Optional[float] = None, budgeted: bool = False,
                    on_retry: Optional[Callable[[int, Exception], None]] = None,
                    sleep: Optional[Callable[[float], None]] = None, clock: Callable[[], float] = time.monotonic) -> T:
    """요청을 몇 번 더 시도한다(기본 5·10초 간격). 마지막 예외는 그대로 올린다.

    retry_if 가 없으면 모든 예외를 다시 시도한다 — 목록 페이지용: law.go.kr Open API 는 멀쩡한 페이지에도
    잠깐 404 를 돌려줄 때가 있어(10-29 행정규칙 127쪽) 3초 간격 2회로는 부족했다.
    상세 페이지는 retry_detail_fetch 를 쓴다(일시 오류만, 시간 상한 포함).
    """
    state = _RetryState(attempts, wait_seconds, retry_if, max_total_seconds, budgeted, on_retry, clock)
    attempt = 0
    while True:
        attempt += 1
        state.begin_attempt()
        try:
            return fn()
        except Exception as exc:
            wait = state.next_wait(attempt, exc)
            if wait is None:
                raise
        finally:
            state.end_attempt()
        (sleep or time.sleep)(wait)


async def call_with_retry_async(make_awaitable: Callable[[], Awaitable[T]], attempts: int = 3, wait_seconds: float = 5.0, *,
                                retry_if: Optional[Callable[[Exception], bool]] = None,
                                max_total_seconds: Optional[float] = None, budgeted: bool = False,
                                on_retry: Optional[Callable[[int, Exception], None]] = None,
                                sleep: Optional[Callable[[float], Awaitable[None]]] = None,
                                clock: Callable[[], float] = time.monotonic) -> T:
    """call_with_retry 의 비동기판. make_awaitable 은 시도마다 새 awaitable 을 만들어 돌려준다(예: lambda: page.goto(url))."""
    state = _RetryState(attempts, wait_seconds, retry_if, max_total_seconds, budgeted, on_retry, clock)
    attempt = 0
    while True:
        attempt += 1
        state.begin_attempt()
        try:
            return await make_awaitable()
        except Exception as exc:
            wait = state.next_wait(attempt, exc)
            if wait is None:
                raise
        finally:
            state.end_attempt()
        await (sleep or asyncio.sleep)(wait)


def _log_detail_retry(attempt: int, exc: Exception) -> None:
    logger.warning("상세 페이지 요청 일시 오류, 다시 시도합니다 (%d/%d): %s", attempt, DETAIL_RETRY_ATTEMPTS, exc)


def retry_detail_fetch(fn: Callable[[], T], **overrides) -> T:
    """상세 페이지를 받는 동기 호출(requests·curl_cffi)을 일시 오류에 한해 다시 시도한다. 소진되면 마지막 예외를 그대로 올린다.

    fn 안에서 raise_for_status() 까지 해야 5xx/4xx 가 구분된다."""
    return call_with_retry(fn, **_detail_options(overrides))


async def retry_detail_fetch_async(make_awaitable: Callable[[], Awaitable[T]], **overrides) -> T:
    """retry_detail_fetch 의 비동기판(예: Playwright page.goto, asyncio.to_thread 로 부른 요청)."""
    return await call_with_retry_async(make_awaitable, **_detail_options(overrides))


def _detail_options(overrides: dict) -> dict:
    options = dict(attempts=DETAIL_RETRY_ATTEMPTS, wait_seconds=DETAIL_RETRY_WAIT_SECONDS,
                   retry_if=is_transient_error, max_total_seconds=DETAIL_RETRY_MAX_SECONDS_PER_CALL,
                   budgeted=True, on_retry=_log_detail_retry)
    options.update(overrides)
    return options
