"""저장된 원문의 깨진 글자·HTML 흔적·조문 제목 뒤 공백을 고친다(기본 미리 보기).

    python3 manual.py fix:text-cleanup --collections case law --samples 3     # 미리 보기
    python3 manual.py fix:text-cleanup --apply

고치는 것: 한글 사이 `？`→`ㆍ`, `<br>`→줄바꿈, `&nbsp;`→공백, 법령 `제N조(제목)` 뒤 공백 2칸 이상→줄바꿈.
바뀐 행은 content_hash·source_version_id 를 다시 계산한다.
공백만 바뀐 행은 임베딩을 건드리지 않고, 글자가 바뀐 판례·행정해석은 대표 행(title+whole)의
embedding_hash 를 지워 03:30 갱신 작업이 다시 임베딩하게 한다.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pymongo import UpdateOne  # noqa: E402

from scripts.core.database.source_versioning import build_source_version_id, sha256_content  # noqa: E402
from scripts.utils.text_cleanup import clean_text  # noqa: E402,F401

BATCH = 500
DEFAULT_COLLECTIONS = ["case", "judgment", "interpretation", "admin_decc", "constitutional_decc",
                       "legislation_expc", "decision", "mediation_case", "law"]
REEMBED_COLLECTIONS = {"case", "interpretation"}  # 문서당 벡터(title+whole) — reparse.py 와 같은 규칙
WHOLE_DOCUMENT_RECIPE = "title+whole@4096"
ACTIVE = {"metadata.is_active": {"$ne": False}}


def letters(text: str) -> str:
    return re.sub(r"\s+", "", text)


def sample(before: str, after: str) -> str:
    start = next((i for i, (a, b) in enumerate(zip(before, after)) if a != b), min(len(before), len(after)))
    lo = max(0, start - 80)
    return f"  전: …{before[lo:start + 80]!r}\n  후: …{after[lo:start + 80]!r}"


def run(args, db, out=print) -> dict:
    totals = {}
    for name in args.collections:
        collection = db[name]
        stat = {"rows": 0, "changed": 0, "reembed_docs": 0, "written": 0}
        totals[name] = stat
        ops, reembed, shown = [], set(), 0
        for row in collection.find(ACTIVE, {"content": 1, "doc_id": 1, "chunk_id": 1}):
            stat["rows"] += 1
            before = row.get("content")
            if not isinstance(before, str):
                continue
            after = clean_text(before, name)
            if after == before:
                continue
            stat["changed"] += 1
            if letters(after) != letters(before) and name in REEMBED_COLLECTIONS:
                reembed.add(row.get("doc_id"))
            if shown < args.samples:
                shown += 1
                out(f"\n== {name} {row.get('doc_id')}\n{sample(before, after)}")
            if args.apply:
                content_hash = sha256_content(after)
                chunk_id = str(row.get("chunk_id") or row.get("doc_id") or "").strip()
                fields = {"content": after, "content_hash": content_hash,
                          "source_version_id": build_source_version_id(name, chunk_id, content_hash)}
                ops.append(UpdateOne({"_id": row["_id"], "content": before}, {"$set": fields}))
                if len(ops) >= BATCH:
                    stat["written"] += collection.bulk_write(ops, ordered=False).modified_count
                    ops = []
        if ops:
            stat["written"] += collection.bulk_write(ops, ordered=False).modified_count
        stat["reembed_docs"] = len(reembed)
        if args.apply:
            for doc_id in reembed:
                collection.update_many({"doc_id": doc_id, "metadata.embed_recipe": WHOLE_DOCUMENT_RECIPE,
                                        **ACTIVE}, {"$unset": {"embedding_hash": ""}})
        out(f"[{name}] 바뀔 행 {stat['changed']}/{stat['rows']}, 재임베딩 문서 {stat['reembed_docs']}")
    out(f"\n요약: {totals}" + ("" if args.apply else "  (미리 보기 — 저장하려면 --apply)"))
    return totals


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="원문의 깨진 글자·HTML 흔적·조문 제목 공백 정리(기본 미리 보기)")
    parser.add_argument("--collections", nargs="+", default=DEFAULT_COLLECTIONS)
    parser.add_argument("--samples", type=int, default=3, help="컬렉션별 표본 수")
    parser.add_argument("--apply", action="store_true", help="실제로 저장한다(없으면 미리 보기)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    from scripts.core.database.mongo_client import get_mongo_db
    run(args, get_mongo_db())
    return 0


if __name__ == "__main__":
    sys.exit(main())
