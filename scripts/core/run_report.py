"""스크래퍼 한 번 실행 중에 생긴 실패 대상을 모은다.

목록 페이지 하나가 실패해도 스크래퍼는 나머지 페이지로 계속 진행한다. 예전에는
이런 실패가 경고 로그로만 남아 일일 결과에서 보이지 않았다(10-14 법제처해석례
179건 중 137건만 수집). 목록 스크래퍼는 실패를 여기에 기록하고, crawl.py가
스크래퍼 결과의 failed_targets 로 내보낸다.

스크래퍼마다 스레드·이벤트 루프가 따로라 contextvars 로 실행 단위를 나눈다.
"""

import contextvars
import time
from typing import Callable, List, Optional, TypeVar

T = TypeVar("T")

_current: contextvars.ContextVar[Optional["RunReport"]] = contextvars.ContextVar(
    "crawl_run_report", default=None
)


class RunReport:
    def __init__(self, scraper_type: str):
        self.scraper_type = scraper_type
        self.failed_targets: List[str] = []
        self.reasons: List[str] = []

    def fail(self, target: str, reason: str) -> None:
        self.failed_targets.append(f"{self.scraper_type}:{target}")
        self.reasons.append(f"{target}: {reason}"[:300])


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


def call_with_retry(fn: Callable[[], T], attempts: int = 2, wait_seconds: float = 3.0) -> T:
    """목록 페이지 요청을 몇 번 더 시도한다. 마지막 예외는 그대로 올린다."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(wait_seconds * attempt)
    raise last_exc
