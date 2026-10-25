"""행정해석 CSV 를 interpretation 컬렉션에 크롤링 결과와 같은 모양으로 넣는다(일회성).

기본은 dry-run(쓰지 않음): 넣을 건수·건너뛸 사유별 건수·샘플을 보여 준다.
이미 수집된 문서(문서번호·날짜 일치)와 법제처 해석례는 건너뛴다.
넣은 행에는 metadata.import_batch 가 붙어 그 값으로 정확히 되돌릴 수 있다.
임베딩은 매일 03:30 임베딩 갱신 작업이 embedding 없는 행을 골라 채운다.

    # dry-run
    python -m scripts.interpretation.runners.import_csv --csv 행정해석_260926.csv
    # 샘플 2건만 실제 저장
    python -m scripts.interpretation.runners.import_csv --csv-url "$URL" --apply --limit 2
    # 전체 저장(이미 넣은 문서는 doc_id 로 건너뛴다)
    python -m scripts.interpretation.runners.import_csv --csv-url "$URL" --apply
"""
import argparse
import csv
import io
import json
import os
import sys
import urllib.request
from collections import Counter
from datetime import datetime

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.append(project_root)

from scripts.interpretation.csv_import import (  # noqa: E402
    DOC_ID_PREFIX,
    ExistingIndex,
    build_documents,
    numbers_from_existing,
    plan_rows,
)

DEFAULT_BATCH = "csv-interpretation-20260926"


def read_rows(path: str = None, url: str = None) -> list:
    if url:
        with urllib.request.urlopen(url, timeout=120) as resp:
            text = resp.read().decode("utf-8-sig")
    else:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    rows = list(csv.DictReader(io.StringIO(text)))
    missing = {"wm_id", "sub_title", "wm_content2"} - set(rows[0].keys() if rows else [])
    if missing:
        raise SystemExit(f"CSV 열이 없습니다: {sorted(missing)}")
    return rows


def load_existing(col) -> tuple:
    index, imported = ExistingIndex(), set()
    projection = {"doc_id": 1, "sub_title": 1, "title": 1, "metadata.doc_number": 1,
                  "metadata.source_type": 1, "metadata.effective": 1}
    for doc in col.find({}, projection).batch_size(2000):
        doc_id = str(doc.get("doc_id") or "")
        if doc_id.startswith(DOC_ID_PREFIX):
            imported.add(doc_id)
            continue
        date = (doc.get("metadata") or {}).get("effective") or None
        for number in numbers_from_existing(doc):
            index.add(number, date)
    return index, imported


def write_documents(col, docs: list, created_at: str) -> None:
    for doc in docs:
        set_fields = {k: v for k, v in doc.items() if k != "metadata"}
        set_fields.update({f"metadata.{k}": v for k, v in doc["metadata"].items()})
        col.update_one(
            {"doc_id": doc["doc_id"], "article_number": doc["article_number"]},
            {"$set": set_fields, "$setOnInsert": {"metadata.created_at": created_at}},
            upsert=True,
        )


def preview(docs: list, width: int = 200) -> dict:
    head = docs[0]
    return {
        "doc_id": head["doc_id"],
        "title": head["title"],
        "sub_title": head["sub_title"],
        "effective": head["metadata"]["effective"],
        "sections": [
            {"article_title": d["article_title"], "chars": len(d["content"]),
             "content": d["content"][:width]}
            for d in docs
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="CSV 파일 경로")
    src.add_argument("--csv-url", help="CSV 를 받을 주소(미리 서명된 S3 주소 등)")
    parser.add_argument("--apply", action="store_true", help="실제로 저장한다(없으면 dry-run)")
    parser.add_argument("--limit", type=int, default=0, help="--apply 때 새로 넣을 최대 문서 수")
    parser.add_argument("--wm-id", action="append", default=[], help="이 wm_id 만 대상으로(여러 번 가능)")
    parser.add_argument("--batch", default=DEFAULT_BATCH, help="metadata.import_batch 값")
    parser.add_argument("--samples", type=int, default=3, help="미리 보여 줄 문서 수")
    args = parser.parse_args(argv)

    rows = read_rows(args.csv, args.csv_url)
    source_file = os.path.basename(args.csv) if args.csv else "행정해석_260926.csv"
    if args.wm_id:
        wanted = {w.strip() for w in args.wm_id}
        rows = [r for r in rows if r["wm_id"].strip() in wanted]

    from scripts.core.database.mongo_client import get_mongo_db
    col = get_mongo_db()["interpretation"]
    existing, imported = load_existing(col)
    plan = plan_rows(rows, existing, imported)

    now = datetime.now()
    to_insert = [row for row, action, _ in plan if action == "insert"]
    reasons = Counter(reason for _, action, reason in plan if action == "skip")
    built = [build_documents(r, now.isoformat(), source_file, args.batch) for r in to_insert]
    no_title = sum(1 for docs in built if docs and docs[0]["title"] == docs[0]["metadata"]["doc_number"])
    no_date = sum(1 for docs in built if docs and not docs[0]["metadata"]["effective"])
    section_titles = Counter(d["article_title"] for docs in built for d in docs)

    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "csv_rows": len(rows),
        "insert_docs": len(built),
        "insert_chunks": sum(len(d) for d in built),
        "skipped": dict(reasons),
        "no_title_used_number": no_title,
        "no_valid_date": no_date,
        "section_titles": dict(section_titles.most_common(15)),
        "import_batch": args.batch,
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
    for docs in built[: args.samples]:
        print("SAMPLE " + json.dumps(preview(docs), ensure_ascii=False))

    if not args.apply:
        print("dry-run 이라 저장하지 않았습니다. --apply 로 실제 저장합니다.")
        return 0

    targets = built[: args.limit] if args.limit > 0 else built
    created_at = now.strftime("%Y-%m-%d")
    written = []
    for docs in targets:
        write_documents(col, docs, created_at)
        written.append(docs[0]["doc_id"])
    print("APPLIED " + json.dumps({"docs": len(written), "doc_ids": written[:50],
                                   "import_batch": args.batch}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
