"""시행 예정본 저장 때 함께 꺼진 현행본을 되살린다(기본 미리 보기).

    python3 manual.py fix:law-current-version                     # 미리 보기
    python3 manual.py fix:law-current-version --apply --backup /path/rows.json

대상: 같은 안정 ID(law_id·adrule_id)에 켜진 현행본이 없고, 켜진 시행 예정본과 doc_id 가 같은 꺼진 현행본이 있는 경우.
같은 공포본이 시행일을 나눠 두면(현행 2026-07-07·예정 2027-01-08) doc_id 가 같아, 예전 저장 코드가 예정본을 새 버전으로 보고 현행본을 껐다.
바꾸는 것: 그 버전 행의 `metadata.is_active` 하나. 본문·임베딩은 그대로다. 다시 돌려도 바뀌지 않는다.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ID_FIELDS = {"law": "law_id", "adrule": "adrule_id"}


def find_versions_to_restore(rows: list[dict], id_field: str, today: str) -> list[dict]:
    """조문 행 요약에서 되살릴 버전(doc_id·시행일)을 고른다. 그룹마다 시행일이 지난 가장 늦은 꺼진 현행본 하나."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        group_id = (row.get("metadata") or {}).get(id_field)
        if group_id:
            groups.setdefault(group_id, []).append(row)

    targets = []
    for group_id, group in groups.items():
        metas = [(r.get("doc_id"), r.get("metadata") or {}) for r in group]
        if any(m.get("is_active") is True and not m.get("is_upcoming") for _, m in metas):
            continue
        upcoming_docs = {doc_id for doc_id, m in metas if m.get("is_active") is True and m.get("is_upcoming")}
        retired = {
            (doc_id, m["effective"])
            for doc_id, m in metas
            if m.get("is_active") is False and not m.get("is_upcoming")
            and (m.get("effective") or "9") <= today
        }
        # 그룹의 가장 최근 현행본이 예정본과 doc_id 가 겹칠 때만이다 — 예전 버전으로 되돌리지 않는다.
        if not retired:
            continue
        doc_id, effective = max(retired, key=lambda version: version[1])
        if doc_id in upcoming_docs:
            targets.append({"group_id": group_id, "doc_id": doc_id, "effective": effective})
    return targets


def _selector(target: dict) -> dict:
    return {"doc_id": target["doc_id"], "metadata.effective": target["effective"],
            "metadata.is_active": False, "metadata.is_upcoming": {"$ne": True}}


def run(args, db, out=print, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    stat = {"versions": 0, "rows": 0, "written": 0}
    backup = []
    for name, id_field in ID_FIELDS.items():
        collection = db[name]
        rows = list(collection.find(
            {f"metadata.{id_field}": {"$exists": True}},
            {"doc_id": 1, f"metadata.{id_field}": 1, "metadata.effective": 1,
             "metadata.is_active": 1, "metadata.is_upcoming": 1},
        ))
        for target in find_versions_to_restore(rows, id_field, today):
            selector = _selector(target)
            ids = [row["_id"] for row in collection.find(selector, {"_id": 1})]
            title = (collection.find_one(selector, {"title": 1}) or {}).get("title")
            stat["versions"] += 1
            stat["rows"] += len(ids)
            out(f"  {name} {title} doc_id={target['doc_id']} 시행 {target['effective']}: {len(ids)}행")
            backup.append({"collection": name, **target, "ids": [str(i) for i in ids]})
            if args.apply:
                stat["written"] += collection.update_many(
                    {"_id": {"$in": ids}}, {"$set": {"metadata.is_active": True}}).modified_count
    if args.apply and args.backup:
        Path(args.backup).write_text(json.dumps(backup, ensure_ascii=False, indent=1), encoding="utf-8")
    out(f"{'적용' if args.apply else '미리 보기'}: {stat}")
    return stat


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="운영 DB 에 쓴다(없으면 미리 보기)")
    parser.add_argument("--backup", help="되살린 행 id 를 적을 파일(되돌릴 때 쓴다)")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply 에는 --backup 이 필요하다")

    from scripts.core.database.mongo_client import get_mongo_db

    run(args, get_mongo_db())


if __name__ == "__main__":
    main()
