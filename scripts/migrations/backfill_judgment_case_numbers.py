"""judgment(주요판정사례) 빈 sub_title 을 판정문 본문에서 복구한다.

배경 (2026-08-19 실측):
  · judgment 411건 중 312건이 sub_title 공백 → FE 출처 라벨이 사건번호 대신
    제목 문장으로 노출.
  · 크롤러는 제목에서만 사건번호를 추출했는데, 구세대 문서의 제목은 서술문이다.
  · nlrc.go.kr 상세 페이지에는 사건번호 필드가 아예 없어(BD_table 실측) 재크롤로
    해결 불가 — 이미 저장된 첨부 판정문 본문이 유일한 소스이며, 여기서 221건이
    복구 가능했다.

Read-only by default. Pass ``--apply`` to update MongoDB.
본문(content)은 건드리지 않으므로 임베딩 재계산이 필요 없다.
"""

from __future__ import annotations

import argparse
from collections import Counter

from scripts.core.database.mongo_client import get_mongo_db
from scripts.utils.reference_sub_title import (
    extract_nlrc_case_number,
    extract_nlrc_case_number_from_body,
)

_EMPTY_SUB_TITLE = {"$or": [
    {"sub_title": None},
    {"sub_title": ""},
    {"sub_title": {"$exists": False}},
]}


def backfill(apply: bool, sample_limit: int = 15) -> Counter:
    db = get_mongo_db()
    collection = db["judgment"]
    stats = Counter()
    samples: list[tuple[str, str]] = []

    doc_ids = collection.distinct("doc_id", _EMPTY_SUB_TITLE)
    for doc_id in doc_ids:
        rows = list(collection.find(
            {"doc_id": doc_id},
            {"title": 1, "content": 1, "sub_title": 1},
        ))
        # 다른 청크에 이미 sub_title 이 있으면(부분 공백) 그 값을 재사용한다.
        existing = next(
            (str(r.get("sub_title")) for r in rows if str(r.get("sub_title") or "").strip()),
            "",
        )
        title = next((r.get("title") for r in rows if r.get("title")), "") or ""
        full_text = "\n".join((r.get("content") or "") for r in rows)

        case_number = (
            existing
            or extract_nlrc_case_number(title)
            or extract_nlrc_case_number_from_body(full_text)
        )

        stats["documents"] += 1
        if not case_number:
            stats["unrecoverable"] += 1
            continue

        stats["recovered"] += 1
        if len(samples) < sample_limit:
            samples.append((str(doc_id), case_number))

        if apply:
            result = collection.update_many(
                {"doc_id": doc_id, **_EMPTY_SUB_TITLE},
                {"$set": {"sub_title": case_number}},
            )
            stats["updated_rows"] += result.modified_count
        else:
            stats["would_update_rows"] += collection.count_documents(
                {"doc_id": doc_id, **_EMPTY_SUB_TITLE}
            )

    print(f"\n== judgment sub_title backfill ({'APPLY' if apply else 'DRY-RUN'}) ==")
    for key in ("documents", "recovered", "unrecoverable", "updated_rows", "would_update_rows"):
        if stats.get(key):
            print(f"  {key}: {stats[key]}")
    print("  sample mappings:")
    for doc_id, case_number in samples:
        print(f"    doc_id={doc_id} → {case_number}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="실제로 MongoDB를 갱신한다")
    args = parser.parse_args()
    backfill(apply=args.apply)


if __name__ == "__main__":
    main()
