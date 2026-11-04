"""Canonical legal-reference subtitle extraction helpers."""

from __future__ import annotations

import re
import unicodedata
from datetime import date


COURT_CASE_NUMBER = re.compile(
    r"\d{2,4}(?:가합|가단|가소|나|다카|다|두|구합|구단|누|고단|고합|"
    r"노|도|마|무|라|구|고정|카합|헌마|헌바|헌가|재다|재두|재누)\d+"
)
NLRC_CASE_NUMBER = re.compile(
    r"((?:중앙|서울|부산|경기|충남|충북|전남|전북|경남|경북|강원|"
    r"제주|인천|대전|대구|광주|울산)\s*\d{4}\s*"
    r"(?:재부해|부해|부노|교섭|공정|차별|단협|조정|사후|교원조정|재단위|단위|휴업)\s*"
    r"\d+(?:\s*[~～-]\s*\d+)?)"
)


def normalize_reference_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip()


def extract_court_reference(title: str) -> str:
    """Return a parenthesized court citation when the official title has one."""
    normalized = normalize_reference_text(title)
    candidates = re.findall(r"[\(\[]([^)\]]+)[\)\]]", normalized)
    for candidate in reversed(candidates):
        if COURT_CASE_NUMBER.search(candidate):
            return candidate.strip()
    return ""


def extract_nlrc_case_number(title: str) -> str:
    """Return the official NLRC case number embedded in a public title."""
    match = NLRC_CASE_NUMBER.search(normalize_reference_text(title))
    return re.sub(r"\s+", "", match.group(1)) if match else ""


# 판정문 헤더의 '사    건' 라벨. normalize_reference_text 가 공백 런을 한 칸으로
# 접으므로 헤더는 "사 건 중앙…"이 되고, 본문 산문의 "이 사건 근로자"("사건",
# 붙어 씀)와 구분된다. 앞뒤에 한글이 붙은 경우는 라벨이 아니다.
_NLRC_BODY_CASE_LABEL = re.compile(r"(?<![가-힣])사 건(?![가-힣])")
_NLRC_BODY_HEAD_CHARS = 6000


def extract_nlrc_case_number_from_body(text: str) -> str:
    """판정문 본문(첨부 PDF 전문 포함)에서 이 문서 자신의 사건번호를 추출한다.

    nlrc.go.kr 상세 페이지에는 사건번호 필드가 아예 없어(실측 2026-08-19,
    BD_table 항목: 자료구분/담당부서/…/판정사항/판정요지/첨부파일) 제목에 번호가
    없는 문서는 첨부 판정문 본문이 유일한 소스다.

    본문에는 초심(지노위) 번호 등 다른 사건의 번호도 인용되므로 순서를 지킨다:
      1) '사 건' 라벨 뒤 120자 안의 번호 — 판정문 헤더의 자기 사건번호
      2) 라벨이 없으면 본문 앞부분(6,000자)의 첫 번째 번호
    """
    normalized = normalize_reference_text(text)
    if not normalized:
        return ""
    head = normalized[:_NLRC_BODY_HEAD_CHARS]

    for label in _NLRC_BODY_CASE_LABEL.finditer(head):
        segment = head[label.end(): label.end() + 120]
        match = NLRC_CASE_NUMBER.search(segment)
        if match:
            return re.sub(r"\s+", "", match.group(1))

    match = NLRC_CASE_NUMBER.search(head)
    return re.sub(r"\s+", "", match.group(1)) if match else ""


def legacy_pdf_case_sub_title(title: str) -> str:
    """Use a legacy PDF judgment title as its citation subtitle when numbered."""
    normalized = normalize_reference_text(title)
    return normalized if COURT_CASE_NUMBER.search(normalized) else ""


_DATE = r"(\d{4})\s*[.년]\s*(\d{1,2})\s*[.월]\s*(\d{1,2})"
# 판정일을 적는 꼴(실측 2026-11-04, 주요판정사례 417건). 앞선 것이 이 문서 자신의 날짜이므로 문서 순서로 본다.
#   [2026. 3. 23. 판정 / 초심: 기각, …] · ■ 판정일자 : 2016. 11. 17. · 판 정 일 2018. 5. 25.
#   결 정 일 2016. 7. 1.(단위 분리 사건) · 일 2018. 5. 23. 판 정 우리 위원회는(본문 추출로 '판 정' 이 밀린 꼴)
_NLRC_DECISION_DATE = re.compile(
    r"\[\s*" + _DATE + r"\s*\.*\s*판\s*정"
    r"|[판결]\s*정\s*일(?:\s*자)?\s*[:：]?\s*" + _DATE
    + r"|" + _DATE + r"\s*\.*\s*판\s*정\s*우리\s*위원회"
)
_FIRST_INSTANCE_TAIL = re.compile(r"초\s*심\s*$")
_CASE_YEAR = re.compile(r"\d{4}")
_NLRC_CENTRAL = "중앙"
_NLRC_PREFIX = re.compile(r"^(중앙|서울|부산|경기|충남|충북|전남|전북|경남|경북|강원|제주|인천|대전|대구|광주|울산)(?=\d)")


def extract_nlrc_decision_date(text: str, *, case_number: str = "", registered_at: str = "") -> str:
    """판정문 본문에서 이 문서 자신의 판정일을 `YYYY-MM-DD` 로 돌려준다. 없으면 빈 문자열.

    상세 페이지의 날짜는 게시판 등록일이라 판정일이 아니다(판정일보다 1~6개월 늦다). 초심 판정일은 건너뛰고,
    등록일보다 늦거나 사건번호 연도보다 이른 값은 본문이 인용한 다른 날짜로 보고 다음 후보를 본다.
    """
    normalized = normalize_reference_text(text)
    year_match = _CASE_YEAR.search(case_number)
    earliest = f"{year_match.group(0)}-01-01" if year_match else ""

    for match in _NLRC_DECISION_DATE.finditer(normalized):
        if _FIRST_INSTANCE_TAIL.search(normalized[max(0, match.start() - 6):match.start()]):
            continue
        year, month, day = (int(part) for part in match.groups() if part)
        try:
            found = date(year, month, day).isoformat()
        except ValueError:
            continue
        if registered_at and found > registered_at:
            continue
        if earliest and found < earliest:
            continue
        return found
    return ""


def nlrc_judgment_sub_title(case_number: str, decision_date: str) -> str:
    """주요판정사례 부제. 판정일이 있으면 위원회·판정일을 앞에 붙인다(`중앙노동위원회 2026. 3. 23. 판정 중앙2025부해1`).

    번호는 지역을 뗀 꼴로 바꾸지 않는다. chat_generation 의 인용 조회가 `중앙2025부해1` 로 부제를 찾는다.
    """
    if not decision_date:
        return case_number
    year, month, day = (int(part) for part in decision_date.split("-"))
    head = f"{year}. {month}. {day}. 판정"
    prefix = _NLRC_PREFIX.match(case_number)
    if not prefix:
        return f"{head} {case_number}".strip()
    name = "중앙노동위원회" if prefix.group(1) == _NLRC_CENTRAL else f"{prefix.group(1)}지방노동위원회"
    return f"{name} {head} {case_number}"
