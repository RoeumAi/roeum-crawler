"""행정규칙에 고시일·고시번호·소관부처·제개정 구분을 채운다 — `python3 manual.py fix:adrule-record-meta`.

일일 크롤링은 이제 저장할 때 상세 API 기본정보(발령일자·발령번호·소관부처명·제개정구분명·행정규칙종류)를
metadata 에 싣는다(scripts/core/lawgokr_stable_id.adrule_basic_meta). 이미 저장된 행은 내용이 바뀌기 전까지 다시
저장되지 않으므로 이 도구로 한 번 채운다. 문서(doc_id)마다 API 를 한 번 부르고, 그 문서의 활성 행 전부에 같은 값을 쓴다.
본문·content_hash·버전은 건드리지 않는다.

본문이 적용 기간을 밝히는 현행·시행예정 행정규칙(최저임금 고시 "적용 기간 : 2027. 1. 1. ~ 2027. 12. 31." 등)에는
끝나는 날(metadata.effective_to)도 채운다 — 일일 수집이 저장할 때 싣는 값과 같다(scraper.save_to_mongodb).
과거 버전(adrule_history)은 넣을 때 다음 버전 시행일 전날을 이미 싣는다.

    python3 manual.py fix:adrule-record-meta            # 미리 보기
    python3 manual.py fix:adrule-record-meta --apply    # 운영 DB 에 쓴다
"""
from __future__ import annotations

import argparse

from pymongo import UpdateMany

from scripts.adrule.logic.attachment_body import stated_period
from scripts.core.lawgokr_stable_id import adrule_basic_meta, fetch_record

COLLECTIONS = ("adrule", "adrule_history")
FIELDS = ("promulgation_date", "promulgation_number", "dept_name", "revision_kind", "rule_kind")
BATCH = 200


def missing_filter() -> dict:
    """활성 행 중 고시일이 아직 없는 행."""
    return {"metadata.is_active": {"$ne": False}, "metadata.promulgation_date": {"$exists": False}}


def set_doc(meta: dict) -> dict:
    return {f"metadata.{k}": v for k, v in meta.items() if k in FIELDS}


def run(args, db, fetch=fetch_record, out=print) -> dict:
    stat = {"docs": 0, "updated_docs": 0, "no_record": 0, "written": 0}
    for name in COLLECTIONS:
        collection = db[name]
        doc_ids = sorted(str(d) for d in collection.distinct("doc_id", missing_filter()))
        if args.limit:
            doc_ids = doc_ids[: args.limit]
        ops = []
        for doc_id in doc_ids:
            stat["docs"] += 1
            values = set_doc(adrule_basic_meta(fetch("admrul", doc_id)))
            if not values.get("metadata.promulgation_date"):
                stat["no_record"] += 1
                continue
            stat["updated_docs"] += 1
            if stat["updated_docs"] <= args.samples:
                out(f"  {name} {doc_id}: {values}")
            ops.append(UpdateMany({"doc_id": doc_id, **missing_filter()}, {"$set": values}))
            if args.apply and len(ops) >= BATCH:
                stat["written"] += collection.bulk_write(ops, ordered=False).modified_count
                ops = []
        if args.apply and ops:
            stat["written"] += collection.bulk_write(ops, ordered=False).modified_count
    stat["effective_to"] = fill_stated_period(args, db["adrule"], out)
    out(f"{'적용' if args.apply else '미리 보기'}: {stat}")
    return stat


def period_filter() -> dict:
    return {"metadata.is_active": {"$ne": False}, "metadata.effective_to": {"$exists": False}}


def fill_stated_period(args, collection, out=print) -> int:
    """끝나는 날이 없는 활성 행정규칙 중 본문이 적용 기간을 밝히고 그 시작이 시행일과 같은 문서에 끝나는 날을 쓴다.
    채운(미리 보기면 채울) 문서 수를 돌려준다."""
    docs: dict[str, dict] = {}
    for row in collection.find(period_filter(), {"doc_id": 1, "content": 1, "metadata.effective": 1}):
        doc = docs.setdefault(str(row["doc_id"]), {"effective": (row.get("metadata") or {}).get("effective"), "text": []})
        doc["text"].append(row.get("content") or "")
    ops = []
    for doc_id, doc in sorted(docs.items()):
        period = stated_period("\n".join(doc["text"]))
        if not period or period[0] != doc["effective"]:
            continue
        if len(ops) < args.samples:
            out(f"  adrule {doc_id}: effective_to={period[1]}")
        ops.append(UpdateMany({"doc_id": doc_id, **period_filter()}, {"$set": {"metadata.effective_to": period[1]}}))
    if args.apply and ops:
        collection.bulk_write(ops, ordered=False)
    return len(ops)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="운영 DB 에 쓴다(없으면 미리 보기)")
    parser.add_argument("--limit", type=int, default=0, help="컬렉션마다 처리할 문서 수 상한(0=전부)")
    parser.add_argument("--samples", type=int, default=5, help="보여 줄 변경 예시 수")
    args = parser.parse_args()
    from scripts.core.database.mongo_client import get_mongo_db

    run(args, get_mongo_db())


if __name__ == "__main__":
    main()
