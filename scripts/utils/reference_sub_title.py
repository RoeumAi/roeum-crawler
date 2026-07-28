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


def legacy_pdf_case_sub_title(title: str) -> str:
    """Use a legacy PDF judgment title as its citation subtitle when numbered."""
    normalized = normalize_reference_text(title)
    return normalized if COURT_CASE_NUMBER.search(normalized) else ""
