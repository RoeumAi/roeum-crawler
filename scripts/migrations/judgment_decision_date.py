"""주요판정사례 부제에 본문의 판정일을 싣고, 놓친 사건번호를 채운다(기본 미리 보기).

    python3 manual.py fix:judgment-decision-date --samples 5    # 미리 보기
    python3 manual.py fix:judgment-decision-date --apply

대상: nlrc.go.kr 에서 수집한 문서(CSV 로 넣은 문서는 부제에 이미 판정일이 있다).
바꾸는 것: `sub_title` 하나. 본문·`metadata.effective`(등록일)·임베딩은 그대로다.
본문에서 판정일을 못 읽으면 기존 사건번호만 유지한다. 저장된 사건번호는 지우지 않는다.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pymongo import UpdateOne  # noqa: E402

from scripts.utils.reference_sub_title import (  # noqa: E402
    extract_nlrc_case_number,
    extract_nlrc_case_number_from_body,
    extract_nlrc_decision_date,
    nlrc_judgment_sub_title,
)

BATCH = 200
TARGET = {"doc_id": {"$regex": r"^\d+$"}, "metadata.source_type": {"$ne": "csv"}}
PROJECTION = {"doc_id": 1, "title": 1, "sub_title": 1, "content": 1, "metadata.effective": 1}
_HAS_DATE = re.compile(r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*판정")


def new_sub_title(row: dict) -> str:
    """이 행의 새 부제. 이미 판정일이 있으면 그대로 돌려줘 다시 돌려도 바뀌지 않는다."""
    current = row.get("sub_title") or ""
    if _HAS_DATE.search(current):
        return current
    content = row.get("content") or ""
    case_number = (
        current
        or extract_nlrc_case_number(row.get("title") or "")
        or extract_nlrc_case_number_from_body(content)
    )
    decision_date = extract_nlrc_decision_date(
        content,
        case_number=case_number,
        registered_at=(row.get("metadata") or {}).get("effective") or "",
    )
    return nlrc_judgment_sub_title(case_number, decision_date)


def run(args, db, out=print) -> dict:
    collection = db["judgment"]
    stat = {"rows": 0, "changed": 0, "date_found": 0, "date_missing": 0, "written": 0}
    ops, shown = [], 0
    for row in collection.find(TARGET, PROJECTION):
        stat["rows"] += 1
        after = new_sub_title(row)
        stat["date_found" if _HAS_DATE.search(after) else "date_missing"] += 1
        before = row.get("sub_title") or ""
        if after == before:
            continue
        stat["changed"] += 1
        if shown < args.samples:
            shown += 1
            registered = (row.get("metadata") or {}).get("effective")
            out(f"  {row.get('doc_id')}: {before!r} -> {after!r} (등록일 {registered})")
        ops.append(UpdateOne({"_id": row["_id"]}, {"$set": {"sub_title": after}}))
        if args.apply and len(ops) >= BATCH:
            stat["written"] += collection.bulk_write(ops, ordered=False).modified_count
            ops = []
    if args.apply and ops:
        stat["written"] += collection.bulk_write(ops, ordered=False).modified_count
    out(f"{'적용' if args.apply else '미리 보기'}: {stat}")
    return stat


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="운영 DB 에 쓴다(없으면 미리 보기)")
    parser.add_argument("--samples", type=int, default=5, help="보여 줄 변경 예시 수")
    args = parser.parse_args()

    from scripts.core.database.mongo_client import get_mongo_db

    run(args, get_mongo_db())


if __name__ == "__main__":
    main()
