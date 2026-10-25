"""CSV(wm_id, sub_title, wm_content2)를 크롤링 문서와 같은 모양으로 넣는다(일회성).

기본은 dry-run(쓰지 않음): 넣을 건수·건너뛸 사유별 건수·샘플을 보여 준다.
이미 수집된 문서는 건너뛰고, 이전에 넣은 CSV 문서는 같은 doc_id 로 다시 쓴다(update).
넣은 행에는 metadata.source_type=csv 와 metadata.import_batch 가 붙어 그 값으로 정확히 되돌릴 수 있다.
임베딩은 매일 03:30 임베딩 갱신 작업이 embedding 없는 행을 골라 채운다.

    python -m scripts.csv_import.run interpretation --csv 행정해석_260926.csv            # dry-run
    python -m scripts.csv_import.run case --csv-url "$URL" --court '대법원|고등법원|행정법원' --apply --limit 2
    python -m scripts.csv_import.run judgment --csv-url "$URL" --apply                  # 전체 저장
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(project_root)

from scripts.csv_import import case, interpretation, judgment  # noqa: E402
from scripts.csv_import.common import has_table, iter_rows  # noqa: E402

MODULES = {"interpretation": interpretation, "case": case, "judgment": judgment}
DEFAULT_FILES = {"interpretation": "행정해석_260926.csv", "case": "판례_260925.csv",
                 "judgment": "주요판정사례_260925.csv"}
DEFAULT_BATCHES = {"interpretation": "csv-interpretation-20260926", "case": "csv-case-20260925",
                   "judgment": "csv-judgment-20260925"}


def make_planner(name: str, col, court: str = None):
    mod = MODULES[name]
    existing, imported = mod.load_existing(col)
    if name == "case":
        return mod.Planner(existing, imported, court)
    return mod.Planner(existing, imported)


def _texts(name: str, built) -> list:
    if name == "interpretation":
        return [d["content"] for d in built]
    if name == "case":
        return [c["text"] for c in built["chunks"]]
    return [built["content"]]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("collection", choices=sorted(MODULES))
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="CSV 파일 경로")
    src.add_argument("--csv-url", help="CSV 를 받을 주소(미리 서명된 S3 주소 등)")
    parser.add_argument("--apply", action="store_true", help="실제로 저장한다(없으면 dry-run)")
    parser.add_argument("--limit", type=int, default=0, help="--apply 때 쓸 최대 문서 수")
    parser.add_argument("--wm-id", action="append", default=[], help="이 wm_id 만 대상으로(여러 번 가능)")
    parser.add_argument("--court", help="판례만: 법원 이름 정규식(이번 단계에 넣을 법원)")
    parser.add_argument("--batch", help="metadata.import_batch 값(기본: csv-<컬렉션>-<CSV 날짜>)")
    parser.add_argument("--samples", type=int, default=3, help="미리 보여 줄 문서 수")
    args = parser.parse_args(argv)

    name, mod = args.collection, MODULES[args.collection]
    source_file = os.path.basename(args.csv) if args.csv else DEFAULT_FILES[name]
    batch = args.batch or DEFAULT_BATCHES[name]
    wanted = {w.strip() for w in args.wm_id}

    from scripts.core.database.mongo_client import get_mongo_db
    col = get_mongo_db()[mod.COLLECTION]
    planner = make_planner(name, col, args.court)

    now = datetime.now()
    created_at = now.strftime("%Y-%m-%d")
    actions, reasons = Counter(), Counter()
    rows_total = written = written_rows = table_docs = 0
    samples, written_ids, failures = [], [], []

    for row in iter_rows(args.csv, args.csv_url):
        if wanted and row["wm_id"].strip() not in wanted:
            continue
        rows_total += 1
        action, reason = planner.decide(row)
        actions[action] += 1
        if action == "skip":
            reasons[reason] += 1
            continue
        built = mod.build_documents(row, now.isoformat(), source_file, batch)
        if any(has_table(t) for t in _texts(name, built)):
            table_docs += 1
        if len(samples) < args.samples:
            samples.append(mod.preview(built))
        if args.apply and (args.limit <= 0 or written < args.limit):
            try:
                written_rows += mod.write(col, built, created_at)
                written += 1
                written_ids.append(mod.doc_id_for(row))
            except Exception as e:  # 한 건 실패로 전체를 멈추지 않는다 — 끝에 모아 보고
                failures.append({"wm_id": row["wm_id"], "error": str(e)[:200]})
            if written and written % 500 == 0:
                print(f"PROGRESS written={written} rows_seen={rows_total}", flush=True)

    summary = {
        "collection": name, "mode": "apply" if args.apply else "dry-run",
        "csv_rows": rows_total, "insert_docs": actions["insert"], "update_docs": actions["update"],
        "skipped": dict(reasons), "docs_with_table": table_docs, "import_batch": batch,
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    for s in samples:
        print("SAMPLE " + json.dumps(s, ensure_ascii=False))
    if not args.apply:
        print("dry-run 이라 저장하지 않았습니다. --apply 로 실제 저장합니다.")
        return 0
    print("APPLIED " + json.dumps({"docs": written, "rows": written_rows, "failed": len(failures),
                                   "doc_ids": written_ids[:50], "import_batch": batch},
                                  ensure_ascii=False))
    for f in failures[:50]:
        print("FAILED " + json.dumps(f, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
