"""Canonical legal-reference subtitle extraction helpers."""

from __future__ import annotations

import re
import unicodedata


COURT_CASE_NUMBER = re.compile(
    r"\d{2,4}(?:가합|가단|가소|나|다카|다|두|구합|구단|누|고단|고합|"
    r"노|도|마|무|라|구|고정|카합|헌마|헌바|헌가|재다|재두|재누)\d+"
)
NLRC_CASE_NUMBER = re.compile(
    r"((?:중앙|서울|부산|경기|충남|충북|전남|전북|경남|경북|강원|"
    r"제주|인천|대전|대구|광주|울산)\s*\d{4}\s*"
    r"(?:부해|부노|교섭|공정|차별|단협|조정|사후|교원조정)\s*"
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
