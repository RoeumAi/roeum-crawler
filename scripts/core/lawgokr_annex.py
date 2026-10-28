"""법령·행정규칙 별표 수집 — 국가법령 Open API 의 별표 원본 파일을 chat_generation 으로 파싱한다.

별표(요율표·기준표·금지 직종 목록 등)는 표가 대부분이라 조문 본문만으로는 빠진다(10-28 기준
별표를 참조하는 법령 64건·행정규칙 150건, 저장된 별표 0건). 원본 PDF 를 받아 다른 첨부와 같은
파서로 읽어 표를 Markdown 표로 남긴다.

- 법령: lawService.do?target=law&MST=<doc_id> 의 `별표.별표단위` 중 별표구분=별표(서식 제외), PDF 우선
- 행정규칙: target=admrul&ID=<doc_id> 의 `첨부파일` 중 이름에 '별표'가 있는 파일, 같은 이름이면 PDF 우선
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
    url: str
    file_name: str


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


def law_annexes(record: dict) -> list[Annex]:
    units = _as_list(((record or {}).get("법령") or {}).get("별표", {}).get("별표단위"))
    annexes = []
    for unit in units:
        if (unit.get("별표구분") or "별표") != "별표":
            continue  # 별지 서식은 빈 양식이라 넣지 않는다
        link = unit.get("별표서식PDF파일링크") or unit.get("별표서식파일링크")
        if not link:
            continue
        number = _annex_number(unit.get("별표번호", ""), unit.get("별표가지번호", ""))
        title = (unit.get("별표제목") or "").strip()
        annexes.append(Annex(
            article_number=f"별표{number}",
            title=f"[별표 {number}] {title}".replace("[별표 ]", "[별표]").strip(),
            url=DOWNLOAD_BASE + link if link.startswith("/") else link,
            file_name=unit.get("별표PDF파일명") or unit.get("별표HWP파일명") or f"별표{number}.pdf",
        ))
    return annexes


ANNEX_NAME = re.compile(r"\[?\s*별표\s*(\d+)?(?:\s*의\s*(\d+))?\s*\]?")


def adrule_annexes(record: dict) -> list[Annex]:
    files = ((record or {}).get("AdmRulService") or {}).get("첨부파일") or {}
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
        annexes.append(Annex(article_number=f"별표{number}" if number else f"별표:{stem}",
                             title=stem, url=link, file_name=name))
    return sorted(annexes, key=lambda a: a.article_number)


def annex_articles(annexes: list[Annex], extract: Optional[Callable[[bytes, str], dict]] = None) -> list[dict]:
    """별표마다 조문과 같은 모양의 행을 만든다. 하나라도 못 읽으면 예외(호출하는 쪽이 실패로 센다)."""
    extract = extract or call_extract_text
    articles = []
    for annex in annexes:
        response = requests.get(annex.url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        response.raise_for_status()
        document = usable_document(extract(response.content, annex.file_name))
        articles.append({
            "article_number": annex.article_number,
            "article_number_numeric": None,
            "article_title": annex.title,
            "content": document["structured_text"].strip(),
            "annex": {"source_file": annex.file_name, "pdf_extraction_status": document.get("extraction_status")},
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
    return {"is_annex": True, "annex_source_file": annex["source_file"],
            "pdf_extraction_status": annex.get("pdf_extraction_status")}
