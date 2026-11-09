"""법령·행정규칙 별표 수집 — 국가법령 Open API 의 별표 원본 파일을 chat_generation 으로 파싱한다.

별표(요율표·기준표·금지 직종 목록 등)는 표가 대부분이라 조문 본문만으로는 빠진다(10-28 기준
별표를 참조하는 법령 64건·행정규칙 150건, 저장된 별표 0건). 원본 PDF 를 받아 다른 첨부와 같은
파서로 읽어 표를 Markdown 표로 남긴다.

- 법령(target=law&MST=)·행정규칙(target=admrul&ID=) 모두 응답의 `별표.별표단위`를 쓴다.
  별표구분이 서식(별지)인 것은 빈 양식이라 뺀다. 파일은 PDF → HWP 순, 파일이 없으면 응답의 `별표내용` 글을 쓴다.
- 행정규칙에 `별표` 항목이 없으면 `첨부파일` 중 이름에 '별표'가 있는 파일을 쓴다.

파서 호출 속도 제한·멈춤은 document_extraction.call_extract_text 가 맡는다. 멈춘 동안 못 읽은 별표는
문서를 실패로 세어(save_incomplete) 다음 실행이 다시 받는다.
"""
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Optional

import requests

from scripts.core.run_report import (
    EXTRACT_SERVER_DOWN, LAWGOKR_API_ERROR, classify_failure, note_failed_document, note_reason,
)
from scripts.utils.document_extraction import ExtractServerUnavailable, call_extract_text, usable_document

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


def adrule_attachment_files(record: Optional[dict], keep: Callable[[str], bool]) -> dict[str, tuple[str, str]]:
    """행정규칙 `첨부파일` 중 읽을 수 있는(PDF·HWP·HWPX) 파일 {이름(확장자 뺀) → (https 주소, 파일 이름)}.
    keep(이름) 이 참인 것만, 첨부 순서대로 담는다. 같은 이름을 PDF·HWP 로 같이 올린 경우 PDF 하나만 남긴다.
    별표 수집(adrule_annexes)과 본문 원문 고르기(scripts/adrule/logic/attachment_body.notice_files)가 함께 쓴다."""
    files = ((record or {}).get("AdmRulService") or {}).get("첨부파일") or {}
    links, names = _as_list(files.get("첨부파일링크")), _as_list(files.get("첨부파일명"))
    by_stem: dict[str, tuple[str, str]] = {}
    for link, name in zip(links, names):
        name = str(name)
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in EXTRACTABLE:
            continue
        stem = name[: -len(suffix)]
        if not keep(stem):
            continue
        if stem not in by_stem or suffix == ".pdf":
            by_stem[stem] = (str(link).replace("http://", "https://"), name)
    return by_stem


def adrule_annexes(record: dict) -> list[Annex]:
    service = (record or {}).get("AdmRulService") or {}
    units = (service.get("별표") or {}).get("별표단위") if isinstance(service.get("별표"), dict) else None
    if units:
        return annexes_from_units(units)
    by_stem = adrule_attachment_files(record, lambda stem: "별표" in stem)
    annexes = []
    for stem, (link, name) in by_stem.items():
        match = ANNEX_NAME.search(stem)
        number = _annex_number(match.group(1) or "", match.group(2) or "") if match else ""
        annexes.append(Annex(article_number=f"별표{number}", title=stem, url=link, file_name=name))
    return unique_numbers(sorted(annexes, key=lambda a: a.article_number))


def download_and_extract(url: str, file_name: Optional[str] = None, fallback_name: str = "",
                         extract: Optional[Callable[[bytes, str], dict]] = None) -> dict:
    """원본 파일을 받아 chat_generation 으로 파싱한다(표는 Markdown 표로 남는다). 실패는 예외로 올린다.

    file_name 이 없으면 응답의 Content-Disposition 에서 구하고, 그것도 없으면 fallback_name 을 쓴다."""
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    response.raise_for_status()
    if file_name is None:
        match = re.search(r'filename="?([^";]+)', response.headers.get("Content-Disposition", ""))
        file_name = requests.utils.unquote(match.group(1)) if match else fallback_name
    return usable_document((extract or call_extract_text)(response.content, file_name))


def annex_articles(annexes: list[Annex], extract: Optional[Callable[[bytes, str], dict]] = None) -> list[dict]:
    """별표마다 조문과 같은 모양의 행을 만든다. 하나라도 못 읽으면 예외(호출하는 쪽이 실패로 센다)."""
    extract = extract or call_extract_text
    articles = []
    for annex in annexes:
        if annex.url:
            document = download_and_extract(annex.url, annex.file_name, extract=extract)
            content, status, source = document["structured_text"].strip(), document.get("extraction_status"), annex.file_name
            blocks = document.get("document_blocks") or []
            metadata = document.get('extraction_metadata') or {}
            failed_pages = metadata.get('failed_page_numbers') or []
            quality = {key: metadata[key] for key in (
                'availability', 'native_text_fallback_page_numbers', 'visual_text_fallback_page_numbers',
                'paragraph_projection', 'page_recovery', 'image_recovery', 'recovery_issues',
                'source_sha256', 'native_warnings') if key in metadata}
        else:
            # 원본 파일이 없는 별표: Open API 가 준 글(선 그림 표)을 그대로 쓴다.
            content, status, source, blocks, failed_pages, quality = annex.text.strip(), "api_text", "", [], [], {}
        articles.append({
            "article_number": annex.article_number,
            "article_number_numeric": None,
            "article_title": annex.title,
            "content": content,
            "annex": {"source_file": source, "pdf_extraction_status": status, "document_blocks": blocks,
                      "failed_pages": failed_pages, "extraction_quality": quality},
        })
    return articles


def record_title(kind: str, record: Optional[dict]) -> str:
    """Open API 응답의 법령명·행정규칙명(실패 문서 목록에 쓴다)."""
    if kind == "law":
        info = ((record or {}).get("법령") or {}).get("기본정보") or {}
        return info.get("법령명_한글") or info.get("법령명한글") or ""
    info = ((record or {}).get("AdmRulService") or {}).get("행정규칙기본정보") or {}
    return info.get("행정규칙명") or ""


def _pages(numbers) -> str:
    return ",".join(str(n) for n in numbers)


def collect_annex_articles(kind: str, record: Optional[dict], logger,
                           extract: Optional[Callable[[bytes, str], dict]] = None,
                           doc_id: Optional[str] = None, title: str = "") -> tuple[list[dict], int]:
    """텍스트가 있는 fallback은 성공, 실제 추출 불가는 실패. 품질 정보는 별도 보존한다."""
    title = title or record_title(kind, record)

    def failed(reason: str, detail: str) -> None:
        note_reason(reason)
        if doc_id:
            note_failed_document(f"doc:{doc_id}", title, reason, detail)

    if record is None:
        logger.error("별표 목록 조회 실패(Open API) — 다음 실행에서 다시 시도")
        failed(LAWGOKR_API_ERROR, "별표 목록 조회 실패")
        return [], 1
    annexes = law_annexes(record) if kind == "law" else adrule_annexes(record)
    if not annexes:
        return [], 0
    articles, failures = [], []
    for item in annexes:
        try:
            articles.extend(annex_articles([item], extract=extract))
        except Exception as e:
            reason = EXTRACT_SERVER_DOWN if isinstance(e, ExtractServerUnavailable) else classify_failure(f"{type(e).__name__}: {e}")
            detail = f"{item.article_number}: {type(e).__name__}: {e}"
            failures.append((reason, detail))
            logger.error(f"별표 파싱 실패: {detail}")
    logger.info(f"📎 별표 {len(articles)}건 파싱")
    partial = [a for a in articles if a['annex']['pdf_extraction_status'] in {'partial', 'failed'}]
    if partial:
        detail = '; '.join(f"{a['article_number']}(미판독 쪽 {_pages(a['annex']['failed_pages'])})" for a in partial)
        logger.warning(f"별표 fallback 저장 {len(partial)}건: {detail} — 사용 가능한 본문 저장, 품질 경고 별도 기록")
    if failures:
        # One upper document failure, while every successful annex is retained.
        failed(failures[0][0], '; '.join(detail for _, detail in failures))
    return articles, int(bool(failures))


def annex_metadata(article: dict) -> dict:
    annex = article.get("annex")
    if not annex:
        return {}
    meta = {"is_annex": True, "annex_source_file": annex["source_file"],
            "pdf_extraction_status": annex.get("pdf_extraction_status"),
            "extraction_quality": annex.get("extraction_quality", {}),
            "unread_page_numbers": annex.get("failed_pages", [])}
    if annex.get("document_blocks"):
        # 법률 DB 화면은 행에 document_blocks 가 있으면 표를 표로 그린다(없으면 본문을 평문으로).
        # document_blocks_scope 는 두지 않는다('section'이면 문서 전체를 이 행 하나로 접는다).
        meta["document_blocks"] = annex["document_blocks"]
    return meta
