"""빠진 metadata.dept_name(기관명)을 기존 행과 같은 값으로 채운다 — `python3 manual.py fix:dept-name`.

법률 DB 내보내기는 기관명을 부제에서 먼저 읽고 없을 때 metadata.dept_name 을 쓴다(행정규칙은 소관부처로 그대로 보인다).
크롤러가 이 칸을 채우지 않던 기간(2026-07~11)에 저장된 행이 비어 있어 같은 컬렉션 안에서 값이 섞여 있었다.
2026-11-16 부터 새로 저장하는 행은 크롤러가 채운다(crawler#57). 이 도구는 그 전에 저장된 행만 채운다.

컬렉션별 값(기존 행에 이미 쓰인 값과 같다):
- constitutional_decc: '헌법재판소'
- legislation_expc: '법제처'
- admin_decc: 부제 첫 낱말(재결 기관, 예 '국민권익위원회'·'경기도행정심판위원회'). 부제가 비면 건너뛴다
- mediation_case: 같은 문서(doc_id)의 다른 행(옛 버전)에 있는 값. 없으면 '중앙노동위원회'(조정사건례는 모두 중앙노동위원회 게시)
- adrule: 같은 문서의 다른 행에 있는 값. 없으면 상세 Open API 의 소관부처명. 그것도 없으면 건너뛴다
- case: '고용노동부'(고용노동부 소관 법령을 참조한 판례라는 표시 — 기존 판례 행의 값)
- judgment: 판정 위원회(scripts.utils.nlrc_commission — 부제·사건번호·본문). 못 읽는 행은 건너뛴다(추측하지 않는다)
본문·content_hash·updated_at 은 건드리지 않는다(공개 페이지 변경 감지·사이트맵 lastmod 가 흔들리지 않게).
큰 컬렉션(판례 44만 행)은 _id 묶음으로 나눠 쓴다.

    python3 manual.py fix:dept-name            # 미리 보기
    python3 manual.py fix:dept-name --apply    # 운영 DB 에 쓴다
"""
from __future__ import annotations

import argparse
from collections import Counter
from typing import Callable, Optional

from pymongo import UpdateMany

from scripts.core.lawgokr_stable_id import adrule_basic_meta, fetch_record
from scripts.core.section_document_scraper import first_word
from scripts.utils.nlrc_commission import commission

BATCH = 500
MISSING = {"metadata.dept_name": {"$exists": False}}
CONSTANT = {"constitutional_decc": "헌법재판소", "legislation_expc": "법제처"}
MEDIATION_DEFAULT = "중앙노동위원회"
COLLECTIONS = ("constitutional_decc", "legislation_expc", "admin_decc", "mediation_case", "adrule", "case", "judgment")
ID_BATCH = 5000


def plan_by_row(name: str, collection) -> tuple[dict[str, list], int]:
    """({값: [_id, ...]}, 빈 행 수) — 행마다 값이 정해지는 큰 컬렉션(판례·판정사례).

    판례는 51만 행이라 count_documents 가 소켓 시간 제한(60초)에 걸린다 → 커서로 읽으며 센다."""
    groups: dict[str, list] = {}
    missing = 0
    projection = {"_id": 1} if name == "case" else {"sub_title": 1, "title": 1, "content": 1}
    for r in collection.find(MISSING, projection):
        missing += 1
        if name == "case":
            value = "고용노동부"
        else:
            value = commission(r.get("sub_title") or "", r.get("title") or "", r.get("content") or "")
        if value:
            groups.setdefault(value, []).append(r["_id"])
    return groups, missing


def sibling_value(collection, doc_id) -> Optional[str]:
    """같은 문서의 다른 행에 있는 기관명(가장 많이 쓰인 값)."""
    values = Counter(str((row.get("metadata") or {}).get("dept_name") or "").strip()
                     for row in collection.find({"doc_id": doc_id, "metadata.dept_name": {"$exists": True}},
                                                {"metadata.dept_name": 1}))
    values.pop("", None)
    return values.most_common(1)[0][0] if values else None


def plan(name: str, collection, fetch: Callable = fetch_record) -> dict[tuple, str]:
    """{(doc_id, sub_title 또는 None): 채울 값}. 키의 sub_title 은 행정심판처럼 행마다 값이 갈릴 때만 쓴다."""
    decisions: dict[tuple, str] = {}
    if name in CONSTANT:
        for doc_id in collection.distinct("doc_id", MISSING):
            decisions[(doc_id, None)] = CONSTANT[name]
    elif name == "admin_decc":
        for row in collection.find(MISSING, {"doc_id": 1, "sub_title": 1}):
            value = first_word(row.get("sub_title") or "")
            if value:
                decisions[(row["doc_id"], row.get("sub_title") or "")] = value
    elif name in ("mediation_case", "adrule"):
        for doc_id in collection.distinct("doc_id", MISSING):
            value = sibling_value(collection, doc_id)
            if not value and name == "mediation_case":
                value = MEDIATION_DEFAULT
            if not value and name == "adrule":
                value = adrule_basic_meta(fetch("admrul", str(doc_id))).get("dept_name")
            if value:
                decisions[(doc_id, None)] = value
    return decisions


def run(args, db, fetch: Callable = fetch_record, out=print) -> dict:
    stat = {}
    for name in COLLECTIONS:
        collection = db[name]
        if name in ("case", "judgment"):
            groups, missing_rows = plan_by_row(name, collection)
            written = 0
            if args.apply:
                for value, ids in groups.items():
                    for start in range(0, len(ids), ID_BATCH):
                        written += collection.update_many({"_id": {"$in": ids[start:start + ID_BATCH]}, **MISSING},
                                                          {"$set": {"metadata.dept_name": value}}).modified_count
            counts = {v: len(ids) for v, ids in sorted(groups.items(), key=lambda kv: -len(kv[1]))}
            stat[name] = {"빈 행": missing_rows, "채울 행": sum(counts.values()), "값": dict(list(counts.items())[:8]),
                          "쓴 행": written if args.apply else None}
            out(f"  {name}: {stat[name]}")
            continue
        missing_rows = collection.count_documents(MISSING)
        decisions = plan(name, collection, fetch)
        ops = []
        for (doc_id, sub_title), value in decisions.items():
            flt = {"doc_id": doc_id, **MISSING}
            if sub_title is not None:
                flt["sub_title"] = sub_title
            ops.append(UpdateMany(flt, {"$set": {"metadata.dept_name": value}}))
        written = 0
        if args.apply:
            for start in range(0, len(ops), BATCH):
                written += collection.bulk_write(ops[start:start + BATCH], ordered=False).modified_count
        values = Counter(decisions.values())
        stat[name] = {"빈 행": missing_rows, "채울 묶음": len(ops), "값": dict(values.most_common(6)),
                      "쓴 행": written if args.apply else None}
        out(f"  {name}: {stat[name]}")
    out(f"{'적용' if args.apply else '미리 보기'} 끝")
    return stat


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="운영 DB 에 쓴다(없으면 미리 보기)")
    args = parser.parse_args()
    from scripts.core.database.mongo_client import get_mongo_db

    run(args, get_mongo_db())


if __name__ == "__main__":
    main()
