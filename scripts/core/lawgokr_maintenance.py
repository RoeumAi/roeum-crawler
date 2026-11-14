"""law.go.kr 시스템 점검 감지.

점검 중에는 law.go.kr 의 모든 주소(목록 페이지·DRF Open API)가 /sorry/sorry.html 로 넘어간다.
스크래퍼는 이 안내 페이지를 JSON·XML 로 읽으려다 실패해 '목록을 못 받음'으로만 남는다(11-14 00:02 실행,
점검 11-13 21:00 ~ 11-14 21:00). 일일 요약이 점검인지와 점검 시간을 보여 줄 수 있게, 실패한 밤에
한 번 물어본다.
"""

import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 점검이면 어느 주소든 안내 페이지로 넘어간다. 인증키 없이 부르는 DRF 검색 주소로 확인한다.
PROBE_URL = "https://www.law.go.kr/DRF/lawSearch.do?target=law&type=XML&query=%EA%B7%BC%EB%A1%9C%EA%B8%B0%EC%A4%80%EB%B2%95"
PROBE_TIMEOUT_SECONDS = 10
SORRY_PATH = "/sorry/"
UNKNOWN_WINDOW = "점검 시간 미표시"

_TITLE_RE = re.compile(r"<title>\s*시스템\s*점검\s*</title>")
_TAG_RE = re.compile(r"<[^>]+>")
# 양쪽을 40자로 묶어 시각이 없는 안내에서 페이지 본문까지 끌려오지 않게 한다.
_WINDOW_RE = re.compile(r"작업\s*\(중단\)\s*시간\s*:\s*([^~]{1,40}?~[^~]{1,40}?\d{1,2}:\d{2})")


def parse_maintenance(url: str, text: str) -> Optional[str]:
    """응답이 점검 안내면 점검 시간(못 읽으면 UNKNOWN_WINDOW), 아니면 None."""
    text = text or ""
    if SORRY_PATH not in (url or "") and not _TITLE_RE.search(text):
        return None
    plain = re.sub(r"\s+", " ", _TAG_RE.sub(" ", text))
    match = _WINDOW_RE.search(plain)
    return match.group(1).strip() if match else UNKNOWN_WINDOW


def probe_maintenance(get: Optional[Callable] = None) -> Optional[str]:
    """law.go.kr 가 지금 점검 중이면 점검 시간, 아니거나 확인하지 못하면 None. 예외를 올리지 않는다."""
    if get is None:
        import requests
        get = requests.get
    try:
        resp = get(PROBE_URL, allow_redirects=True, timeout=PROBE_TIMEOUT_SECONDS)
        return parse_maintenance(str(getattr(resp, "url", "")), getattr(resp, "text", ""))
    except Exception as exc:  # noqa: BLE001 - 확인 실패가 보고를 막지 않게
        logger.warning("law.go.kr 점검 여부 확인 실패: %s", type(exc).__name__)
        return None
