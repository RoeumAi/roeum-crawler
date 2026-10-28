"""법령·행정규칙 별표 수집 — 국가법령 Open API 의 별표 원본 파일을 chat_generation 으로 파싱한다.

별표(요율표·기준표·금지 직종 목록 등)는 표가 대부분이라 조문 본문만으로는 빠진다(10-28 기준
별표를 참조하는 법령 64건·행정규칙 150건, 저장된 별표 0건). 원본 PDF 를 받아 다른 첨부와 같은
파서로 읽어 표를 Markdown 표로 남긴다.

- 법령(target=law&MST=)·행정규칙(target=admrul&ID=) 모두 응답의 `별표.별표단위`를 쓴다.
  별표구분이 서식(별지)인 것은 빈 양식이라 뺀다. 파일은 PDF → HWP 순, 파일이 없으면 응답의 `별표내용` 글을 쓴다.
- 행정규칙에 `별표` 항목이 없으면 `첨부파일` 중 이름에 '별표'가 있는 파일을 쓴다.
"""
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Optional

import requests

from scripts.utils.document_extraction import call_extract_text, usable_document

DOWNLOAD_BASE = "https://www.law.go.kr"
EXTRACTABLE = (".pdf", ".hwpx", ".hwp")


@dataclass(frozen=True)
class Annex:
    article_number: str   # "별표1", "별표1의2", 번호 없는 별표는 "별표"
    title: str            # "[별표 1] 과태료의 부과기준(제22조제1항 관련)"
    url: str              # 원본 파일. 없으면 ""(→ text 사용)
    file_name: str
    text: str = ""        # API 가 준 별표 글(파일이 없을 때만 씀)


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _annex_number(number: str, branch: str = "") -> str:
    """API 의 별표번호 '0001'·가지번호 '02' → '1'·'1의2'. 번호 없음('0000')은 ''."""
    main = int(number) if str(number).strip().isdigit() else 0
    sub = int(branch) if str(branch).strip().isdigit() else 0
    if not main:
        return ""
    return f"{main}의{sub}" if sub else str(main)


def _flatten(value) -> str:
    if isinstance(value, list):
        return "\n".join(_flatten(v) for v in value)
    return str(value or "").rstrip()


def annexes_from_units(units) -> list[Annex]:
    annexes = []
    for unit in _as_list(units):
        if (unit.get("별표구분") or "별표") != "별표":
            continue  # 별지 서식은 빈 양식이라 넣지 않는다
        number = _annex_number(unit.get("별표번호", ""), unit.get("별표가지번호", ""))
        title = (unit.get("별표제목") or "").strip()
        pdf, hwp = unit.get("별표서식PDF파일링크"), unit.get("별표서식파일링크")
        link = pdf or hwp or ""
        text = _flatten(unit.get("별표내용")) if not link else ""
        if not link and not text.strip():
            continue
        annexes.append(Annex(
            article_number=f"별표{number}",
            title=f"[별표 {number}] {title}".replace("[별표 ]", "[별표]").strip(),
            url=(DOWNLOAD_BASE + link if link.startswith("/") else link),
            file_name=(unit.get("별표PDF파일명") if pdf else unit.get("별표HWP파일명")) or f"별표{number}.{'pdf' if pdf else 'hwp'}",
            text=text,
        ))
    return unique_numbers(annexes)


def unique_numbers(annexes: list[Annex]) -> list[Annex]:
    """같은 번호(번호 없는 별표 여럿 등)는 순서대로 -2, -3 을 붙인다. 겹치면 서로 덮어써 매번 새 버전이 생긴다."""
    seen: dict[str, int] = {}
    result = []
    for annex in annexes:
        count = seen.get(annex.article_number, 0) + 1
        seen[annex.article_number] = count
        if count > 1:
            annex = Annex(f"{annex.article_number}-{count}", annex.title, annex.url, annex.file_name, annex.text)
        result.append(annex)
    return result


def law_annexes(record: dict) -> list[Annex]:
    return annexes_from_units((((record or {}).get("법령") or {}).get("별표") or {}).get("별표단위"))


ANNEX_NAME = re.compile(r"\[?\s*별표\s*(\d+)?(?:\s*의\s*(\d+))?\s*\]?")


def adrule_annexes(record: dict) -> list[Annex]:
    service = (record or {}).get("AdmRulService") or {}
    units = (service.get("별표") or {}).get("별표단위") if isinstance(service.get("별표"), dict) else None
    if units:
        return annexes_from_units(units)
    files = service.get("첨부파일") or {}
    links, names = _as_list(files.get("첨부파일링크")), _as_list(files.get("첨부파일명"))
    by_stem: dict[str, tuple[str, str]] = {}
    for link, name in zip(links, names):
        suffix = PurePosixPath(name).suffix.lower()
        if "별표" not in name or suffix not in EXTRACTABLE:
            continue
        stem = name[: -len(suffix)]
        # 같은 별표를 PDF·HWP 로 같이 올린 경우 PDF 하나만 읽는다.
        if stem not in by_stem or suffix == ".pdf":
            by_stem[stem] = (link.replace("http://", "https://"), name)
    annexes = []
    for stem, (link, name) in by_stem.items():
        match = ANNEX_NAME.search(stem)
        number = _annex_number(match.group(1) or "", match.group(2) or "") if match else ""
        annexes.append(Annex(article_number=f"별표{number}", title=stem, url=link, file_name=name))
    return unique_numbers(sorted(annexes, key=lambda a: a.article_number))


def annex_articles(annexes: list[Annex], extract: Optional[Callable[[bytes, str], dict]] = None) -> list[dict]:
    """별표마다 조문과 같은 모양의 행을 만든다. 하나라도 못 읽으면 예외(호출하는 쪽이 실패로 센다)."""
    extract = extract or call_extract_text
    articles = []
    for annex in annexes:
        if annex.url:
            response = requests.get(annex.url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            response.raise_for_status()
            document = usable_document(extract(response.content, annex.file_name))
            content, status, source = document["structured_text"].strip(), document.get("extraction_status"), annex.file_name
            blocks = document.get("document_blocks") or []
        else:
            # 원본 파일이 없는 별표: Open API 가 준 글(선 그림 표)을 그대로 쓴다.
            content, status, source, blocks = annex.text.strip(), "api_text", "", []
        articles.append({
            "article_number": annex.article_number,
            "article_number_numeric": None,
            "article_title": annex.title,
            "content": content,
            "annex": {"source_file": source, "pdf_extraction_status": status, "document_blocks": blocks},
        })
    return articles


def collect_annex_articles(kind: str, record: Optional[dict], logger,
                           extract: Optional[Callable[[bytes, str], dict]] = None) -> tuple[list[dict], int]:
    """(별표 행, 실패 수). Open API 를 못 읽었거나 별표 하나라도 못 읽으면 실패 1 —
    조문은 그대로 저장하되 문서를 실패로 세어 save_incomplete 표시로 다음 실행이 다시 받게 한다."""
    if record is None:
        logger.error("별표 목록 조회 실패(Open API) — 다음 실행에서 다시 시도")
        return [], 1
    annexes = law_annexes(record) if kind == "law" else adrule_annexes(record)
    if not annexes:
        return [], 0
    try:
        articles = annex_articles(annexes, extract=extract)
    except Exception as e:  # noqa: BLE001
        logger.error(f"별표 파싱 실패: {type(e).__name__}: {e}")
        return [], 1
    logger.info(f"📎 별표 {len(articles)}건 파싱")
    return articles, 0


def annex_metadata(article: dict) -> dict:
    annex = article.get("annex")
    if not annex:
        return {}
    meta = {"is_annex": True, "annex_source_file": annex["source_file"],
            "pdf_extraction_status": annex.get("pdf_extraction_status")}
    if annex.get("document_blocks"):
        # 법률 DB 화면은 행에 document_blocks 가 있으면 표를 표로 그린다(없으면 본문을 평문으로).
        # document_blocks_scope 는 두지 않는다('section'이면 문서 전체를 이 행 하나로 접는다).
        meta["document_blocks"] = annex["document_blocks"]
    return meta
