"""PDF 전문이 없는 judgment(주요판정사례) 문서의 첨부 본문을 재추출한다.

배경 (2026-08-20):
  · OCR 키 크레딧 소진(429) 기간에 크롤링된 문서들은 content 에 HTML 요약
    (판정사항/판정요지)만 있고 [첨부파일 전문] 이 없다. 재시도 한도
    (MAX_PDF_RETRIES) 소진으로 pdf_retry_needed=False 가 되어 일일 경로로는
    다시 시도되지 않는다.
  · 키 정상화 후 이 스크립트로 해당 문서만 골라 재추출한다.

왜 scrape_and_save 를 그대로 안 쓰나:
  · 레거시 문서는 doc_id 가 정수형("1","100")인데 scrape_and_save 는 URL 의
    jgmtSn 으로 doc_id 를 새로 만든다 — 그대로 돌리면 같은 문서가 두 doc_id 로
    **중복 생성**된다. 여기서는 기존 doc_id/chunk_id/article_number 를 유지한 채
    제자리 갱신한다.
  · $set 은 dotted metadata 경로(build_mongo_set_fields)로 만들어, 임베딩
    파이프라인이 관리하는 metadata 키(is_searchable, embedding_* 등)를 보존한다.
    content_hash 가 갱신되면 임베딩 리프레시가 변경을 감지해 재임베딩한다.

Read-only by default. Pass ``--apply`` to fetch/OCR and update MongoDB.
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime

from scripts.core.database.mongo_client import get_mongo_db
from scripts.core.database.source_versioning import (
    build_mongo_set_fields,
    sha256_content,
)
from scripts.judgment.logic.scraper import _fetch_detail_html, _parse_detail
from scripts.utils.logger_config import get_logger
from scripts.utils.nlrc_pdf import extract_attachment_text
from scripts.utils.pdf_text_cleaner import clean_pdf_artifacts
from scripts.utils.reference_sub_title import (
    extract_nlrc_case_number,
    extract_nlrc_case_number_from_body,
)

logger = get_logger(__name__, scraper_type="judgment")

_NO_PDF = {"content": {"$not": re.compile(r"\[첨부파일 전문\]")}}
_FETCH_DELAY_SECONDS = 2.0


def find_targets(collection) -> list[dict]:
    return list(collection.find(
        _NO_PDF,
        {
            "doc_id": 1, "chunk_id": 1, "article_number": 1, "title": 1,
            "sub_title": 1, "metadata.source_url": 1, "metadata.pdf_error": 1,
        },
    ))


def reextract_one(collection, row: dict) -> str:
    """한 문서를 재추출·갱신한다. 반환: 'updated' | 'ocr_failed' | 'no_url'."""
    doc_id = row.get("doc_id")
    source_url = ((row.get("metadata") or {}).get("source_url") or "").strip()
    if not source_url:
        logger.warning(f"⚠️ {doc_id}: source_url 없음 — 건너뜀")
        return "no_url"

    html = _fetch_detail_html(source_url)
    parsed = _parse_detail(html)
    pdf_result = extract_attachment_text(html, source_url)
    if not pdf_result.get("success"):
        logger.warning(f"⚠️ {doc_id}: 재추출 실패 — {str(pdf_result.get('error'))[:120]}")
        return "ocr_failed"

    html_content = "\n\n".join([
        f"판정사항: {parsed.get('judgment_points', '')}",
        f"판정요지: {parsed.get('judgment_summary', '')}",
    ]).strip()
    content = clean_pdf_artifacts(
        f"{html_content}\n\n[첨부파일 전문]\n{pdf_result['text']}".strip()
    )
    sub_title = (
        str(row.get("sub_title") or "").strip()
        or extract_nlrc_case_number(parsed.get("title") or "")
        or extract_nlrc_case_number_from_body(content)
    )
    attachment = pdf_result.get("attachment") or {}
    now = datetime.now().isoformat()

    set_fields = build_mongo_set_fields(
        {
            # 식별자(doc_id/chunk_id/article_number)는 기존 값 유지 — $set 에 안 넣는다.
            "title": parsed.get("title") or row.get("title"),
            "sub_title": sub_title,
            "content": content,
            "content_hash": sha256_content(content),
        },
        {
            "updated_at": now,
            "attachment_name": attachment.get("name"),
            "attachment_url": attachment.get("download_url"),
            "attachment_file_id": attachment.get("file_id"),
            "content_source": pdf_result.get("content_source"),
            "pdf_page_count": pdf_result.get("page_count"),
            "pdf_is_searchable": pdf_result.get("is_searchable"),
            "pdf_cost_usd": pdf_result.get("cost_usd", 0.0),
            "pdf_error": "",
            "pdf_retry_needed": False,
            "pdf_retry_count": 0,
        },
    )
    result = collection.update_many({"doc_id": doc_id}, {"$set": set_fields})
    logger.info(
        f"✅ {doc_id}: 재추출 완료 ({pdf_result.get('content_source')}, "
        f"{len(pdf_result.get('text') or ''):,}자, sub_title={sub_title or '없음'}, "
        f"rows={result.modified_count})"
    )
    return "updated"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="실제로 재추출·갱신한다")
    parser.add_argument("--limit", type=int, default=0, help="처리 문서 수 상한 (0=전부)")
    args = parser.parse_args()

    collection = get_mongo_db()["judgment"]
    targets = find_targets(collection)
    print(f"== judgment PDF 재추출 ({'APPLY' if args.apply else 'DRY-RUN'}) — 대상 {len(targets)}건 ==")
    for row in targets:
        err = str((row.get("metadata") or {}).get("pdf_error") or "")[:60]
        print(f"  doc_id={row.get('doc_id')} title={str(row.get('title'))[:40]} pdf_error={err or '-'}")

    if not args.apply:
        return

    if args.limit > 0:
        targets = targets[: args.limit]

    stats = {"updated": 0, "ocr_failed": 0, "no_url": 0, "error": 0}
    for i, row in enumerate(targets, start=1):
        try:
            outcome = reextract_one(collection, row)
        except Exception as e:  # 한 건 실패가 전체를 멈추지 않게
            logger.error(f"❌ {row.get('doc_id')}: {type(e).__name__}: {e}")
            outcome = "error"
        stats[outcome] += 1
        print(f"[{i}/{len(targets)}] doc_id={row.get('doc_id')} → {outcome}")
        time.sleep(_FETCH_DELAY_SECONDS)

    print(f"\n결과: {stats}")


if __name__ == "__main__":
    main()
