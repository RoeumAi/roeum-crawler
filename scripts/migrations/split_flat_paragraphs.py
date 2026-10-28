"""원본에 줄바꿈이 없어 한 줄로 들어간 본문을 번호 앞에서 문단으로 나눈다(원문을 다시 받을 수 없는 CSV 수입분용).

    python3 manual.py fix:split-paragraphs --collection case --samples 5      # 미리 보기
    python3 manual.py fix:split-paragraphs --collection case --apply

대상: 지정 컬렉션의 활성 행 중 metadata.source_type(기본 csv)이 맞고, 800자 넘는데 줄바꿈이 없는 행.
웹 수집분은 원문에 문단이 있으므로 fix:rescrape-paragraphs 를 쓴다.
공백만 빈 줄로 바꾸므로(글자 그대로) 임베딩은 다시 계산되지 않는다. 바뀌지 않는 행은 쓰지 않는다.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pymongo import UpdateOne  # noqa: E402

from scripts.utils.paragraph_split import same_text_ignoring_space, split_paragraphs  # noqa: E402

BATCH = 500


def flat_query(source_type: str) -> dict:
    return {
        "metadata.is_active": True,
        "metadata.source_type": source_type,
        "content": {"$not": re.compile("\n")},
        "$expr": {"$gt": [{"$strLenCP": {"$ifNull": ["$content", ""]}}, 800]},
    }


def run(args, db, out=print) -> dict:
    collection = db[args.collection]
    query = flat_query(args.source_type)
    totals = {"rows": 0, "changed": 0, "unchanged": 0, "written": 0}
    ops = []
    shown = 0
    cursor = collection.find(query, {"content": 1, "doc_id": 1, "doc_type": 1})
    if args.limit:
        cursor = cursor.limit(args.limit)
    for row in cursor:
        totals["rows"] += 1
        before = row["content"]
        after = split_paragraphs(before)
        if after == before:
            totals["unchanged"] += 1
            continue
        assert same_text_ignoring_space(before, after), row["_id"]  # 글자는 그대로
        totals["changed"] += 1
        if shown < args.samples:
            shown += 1
            out(f"\n== {row.get('doc_id')} [{row.get('doc_type')}] 문단 1 → {after.count(chr(10) * 2) + 1}")
            out(after[:600] + ("…" if len(after) > 600 else ""))
        if args.apply:
            ops.append(UpdateOne({"_id": row["_id"], "content": before}, {"$set": {"content": after}}))
            if len(ops) >= BATCH:
                totals["written"] += collection.bulk_write(ops, ordered=False).modified_count
                ops = []
    if ops:
        totals["written"] += collection.bulk_write(ops, ordered=False).modified_count
    out(f"\n요약: {totals}" + ("" if args.apply else "  (미리 보기 — 저장하려면 --apply)"))
    return totals


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="한 줄로 들어간 본문을 번호 앞에서 문단으로 나눈다(기본 미리 보기)")
    parser.add_argument("--collection", default="case")
    parser.add_argument("--source-type", default="csv", help="metadata.source_type (기본 csv)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--samples", type=int, default=3, help="미리 보여 줄 행 수")
    parser.add_argument("--apply", action="store_true", help="실제로 저장한다(없으면 미리 보기)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    from scripts.core.database.mongo_client import get_mongo_db
    run(args, get_mongo_db())
    return 0


if __name__ == "__main__":
    sys.exit(main())
