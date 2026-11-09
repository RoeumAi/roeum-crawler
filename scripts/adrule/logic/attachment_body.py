"""행정규칙 본문을 첨부 원문으로 읽어야 하는지 판정하고, 읽을 첨부를 고른다(순수 함수).

law.go.kr 상세 화면은 표를 그림(<img>)으로 싣거나 "자세한 내용은 상단 메뉴 … 버튼을 이용하십시오" 한 줄만 보여 주는
행정규칙이 있다. 화면 글만 저장하면 금액·기준표가 빠진 채 "성공"으로 남는다(2008~2023 최저임금 고시 16건,
대지급금 상한액·장애인 고용부담금 기초액 등 현행 30여 건, 11-09 조사). 이때는 첨부파일 가운데 고시 원문을 읽는다.

- 조문 형식(조문형식여부=Y)이고 그림이 섞인 문서는 조문 단위 화면 파서를 그대로 쓴다(조문 일부의 그림일 뿐이고, 첨부는
  개정안·이유서가 섞여 있어 원문을 고르기 어렵다). 본문 자체가 "첨부를 보라"는 문구뿐이면 형식과 관계없이 첨부를 읽는다.
- 첨부 가운데 별표(별표 수집이 따로 읽는다)·이유서·신구조문 대비표·검토 의견서 같은 부속 문서는 뺀다.
  "개정안"·"고시안"·"(안)" 이름은 다른 후보가 있으면 뺀다. 원문을 안(案) 이름으로만 올린 문서도 있어서다
  ("…일부개정안_개정본문.hwp", 2009년 최저임금 "최저임금_결정_고시안.hwp").
  같은 파일을 PDF·HWP 로 함께 올렸으면 PDF 하나만 읽는다(별표 수집과 같은 규칙).
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import PurePosixPath
from typing import Optional

from scripts.core.lawgokr_annex import EXTRACTABLE

POINTER_TO_ATTACHMENT = re.compile(r"자세한\s*내용은.{0,40}버튼을\s*이용")
EMBEDDED_IMAGE = re.compile(r"<img\b", re.IGNORECASE)
# 고시 원문이 아닌 부속 첨부.
SUPPLEMENT_NAME = re.compile(r"별표|이유서|신구|대비표|대조표|의견서|검토|확인증|설명자료|참고자료|양식")
# 확정 전 안(案) 이름. 다른 후보가 없을 때만 원문으로 쓴다.
DRAFT_NAME = re.compile(r"개정안|고시안|\(안\)")


def _service(record: Optional[dict]) -> dict:
    return (record or {}).get("AdmRulService") or {}


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _body_html(record: Optional[dict]) -> str:
    body = _service(record).get("조문내용") or ""
    return " ".join(map(str, body)) if isinstance(body, list) else str(body)


def needs_attachment_body(record: Optional[dict]) -> bool:
    """화면 본문이 첨부를 가리키는 문구뿐이거나, 조문 형식이 아닌 본문에 그림(표)이 들어 있으면 True."""
    body = _body_html(record)
    if POINTER_TO_ATTACHMENT.search(body):
        return True
    structured = (_service(record).get("행정규칙기본정보") or {}).get("조문형식여부") == "Y"
    return not structured and bool(EMBEDDED_IMAGE.search(body))


def notice_files(record: Optional[dict]) -> list[tuple[str, str]]:
    """고시 원문 첨부 [(https 주소, 파일 이름)]. 첨부 순서를 지키고, 같은 이름의 PDF·HWP 는 PDF 하나로 줄인다."""
    files = _service(record).get("첨부파일") or {}
    links, names = _as_list(files.get("첨부파일링크")), _as_list(files.get("첨부파일명"))
    by_stem: dict[str, tuple[str, str]] = {}
    for link, name in zip(links, names):
        name = str(name)
        suffix = PurePosixPath(name).suffix.lower()
        stem = name[: -len(suffix)] if suffix else name
        if suffix not in EXTRACTABLE or SUPPLEMENT_NAME.search(stem):
            continue
        if stem not in by_stem or suffix == ".pdf":
            by_stem[stem] = (str(link).replace("http://", "https://"), name)
    final = {stem: file for stem, file in by_stem.items() if not DRAFT_NAME.search(stem)}
    return list((final or by_stem).values())


_DATE = r"(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?"
STATED_PERIOD = re.compile(rf"적용\s*기간\s*[:：]?\s*{_DATE}\s*[~～∼\-–]\s*{_DATE}")


def stated_period(text: str) -> Optional[tuple[str, str]]:
    """본문에 적힌 적용 기간("적용 기간 : 2027. 1. 1. ~ 2027. 12. 31.") → ("2027-01-01", "2027-12-31"). 없거나 날짜가 틀리면 None."""
    match = STATED_PERIOD.search(text or "")
    if not match:
        return None
    try:
        start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
    except ValueError:
        return None
    return (start.isoformat(), end.isoformat()) if start <= end else None
