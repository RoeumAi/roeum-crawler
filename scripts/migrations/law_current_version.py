"""같은 doc_id 를 함께 쓰는 법령 버전을 나누고, 그 때문에 꺼진 현행본을 되살린다(기본 미리 보기).

    python3 manual.py fix:law-current-version
    python3 manual.py fix:law-current-version --apply --backup /path/rows.json

같은 공포본이 시행일을 나눠 두면(현행 2026-07-07·예정 2027-01-08) doc_id 와 chunk_id 가 같아, 저장 코드가 다른 시행일을
새 버전으로 보고 앞선 버전을 끄거나 두 버전이 함께 켜졌다.
- 나누기: 현행본(시행일이 지난 가장 늦은 버전)이 doc_id 를 유지하고, 나머지 버전은 `doc_id-시행일` 로 바꾼다.
- 되살리기: 켜진 현행본이 없고, 가장 늦은 꺼진 현행본이 켜진 예정본과 doc_id 를 함께 쓸 때만 켠다. 예정본을 비교한 update_summary 는 지운다.
본문·임베딩은 그대로다. 이미 나뉜 뒤에는 대상이 없어 다시 돌려도 바뀌지 않는다.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pymongo import UpdateOne  # noqa: E402

from scripts.law.logic.version_doc_id import version_doc_id  # noqa: E402

BATCH = 200
ROW_FIELDS = {"doc_id": 1, "chunk_id": 1, "article_number": 1, "metadata.law_id": 1, "metadata.effective": 1,
              "metadata.is_active": 1, "metadata.is_upcoming": 1, "metadata.parent_chunk_id": 1,
              "metadata.search_only": 1, "metadata.update_summary": 1}


def _meta(row: dict) -> dict:
    return row.get("metadata") or {}


def plan_version_splits(rows: list[dict], today: str) -> list[dict]:
    """doc_id 하나에 시행일이 둘 이상이면 현행본(없으면 가장 이른 버전)만 남기고 나머지를 옮길 계획을 돌려준다."""
    by_doc: dict[str, set[str]] = {}
    for row in rows:
        effective = _meta(row).get("effective")
        if row.get("doc_id") and effective:
            by_doc.setdefault(row["doc_id"], set()).add(effective)

    splits = []
    for doc_id, effectives in by_doc.items():
        if len(effectives) < 2:
            continue
        passed = [e for e in effectives if e <= today]
        owner = max(passed) if passed else min(effectives)
        splits += [{"doc_id": doc_id, "effective": e, "new_doc_id": version_doc_id(doc_id, e)}
                   for e in sorted(effectives) if e != owner]
    return splits


def find_versions_to_restore(rows: list[dict], today: str) -> list[dict]:
    """그룹마다 되살릴 현행본 하나. 켜진 현행본이 있거나 다른 이유로 꺼진 버전이면 고르지 않는다."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        group_id = _meta(row).get("law_id")
        if group_id:
            groups.setdefault(group_id, []).append(row)

    targets = []
    for group_id, group in groups.items():
        metas = [(r.get("doc_id"), _meta(r)) for r in group]
        if any(m.get("is_active") is True and not m.get("is_upcoming") for _, m in metas):
            continue
        retired = {(doc_id, m["effective"]) for doc_id, m in metas
                   if m.get("is_active") is False and not m.get("is_upcoming")
                   and m.get("effective") and m["effective"] <= today}
        if not retired:
            continue
        doc_id, effective = max(retired, key=lambda version: version[1])
        upcoming_docs = {d for d, m in metas if m.get("is_active") is True and m.get("is_upcoming")}
        if doc_id in upcoming_docs:
            targets.append({"group_id": group_id, "doc_id": doc_id, "effective": effective})
    return targets


def rows_to_restore(rows: list[dict], target: dict) -> list[dict]:
    """되살릴 행. 별표 검색 조각은 일부러 끈 것일 수 있어 빼고, 같은 조문이 겹치면 고를 수 없어 오류로 멈춘다."""
    picked = [r for r in rows if r.get("doc_id") == target["doc_id"]
              and _meta(r).get("effective") == target["effective"]
              and _meta(r).get("is_active") is False and not _meta(r).get("is_upcoming")
              and not _meta(r).get("search_only")]
    numbers = [str(r.get("article_number")) for r in picked]
    if len(numbers) != len(set(numbers)):
        raise ValueError(f"{target['doc_id']} {target['effective']}: 같은 조문 행이 겹쳐 되살릴 수 없다")
    return picked


def _renamed(value, doc_id: str, new_doc_id: str):
    return value.replace(f":{doc_id}:", f":{new_doc_id}:") if isinstance(value, str) else value


def split_update(row: dict, new_doc_id: str) -> dict:
    """버전을 옮기는 $set. chunk_id 와 별표 검색 조각의 부모 chunk_id 도 함께 바꾼다."""
    doc_id = row["doc_id"]
    fields = {"doc_id": new_doc_id}
    if row.get("chunk_id"):
        fields["chunk_id"] = _renamed(row["chunk_id"], doc_id, new_doc_id)
    parent = _meta(row).get("parent_chunk_id")
    if parent:
        fields["metadata.parent_chunk_id"] = _renamed(parent, doc_id, new_doc_id)
    return fields


def _backup_row(row: dict) -> dict:
    meta = _meta(row)
    return {"_id": str(row["_id"]), "doc_id": row.get("doc_id"), "chunk_id": row.get("chunk_id"),
            "parent_chunk_id": meta.get("parent_chunk_id"), "is_active": meta.get("is_active"),
            "update_summary": meta.get("update_summary")}


def run(args, db, out=print, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    collection = db["law"]
    rows = list(collection.find({}, ROW_FIELDS))
    stat = {"splits": 0, "split_rows": 0, "restores": 0, "restore_rows": 0, "written": 0}
    ops, backup = [], []

    # 되살릴 대상은 나누기 전 doc_id 로 고른다(나눈 뒤에는 예정본과 doc_id 가 겹치지 않는다).
    for split in plan_version_splits(rows, today):
        moved = [r for r in rows if r.get("doc_id") == split["doc_id"]
                 and _meta(r).get("effective") == split["effective"]]
        stat["splits"] += 1
        stat["split_rows"] += len(moved)
        out(f"  나누기 {split['doc_id']} 시행 {split['effective']} -> {split['new_doc_id']}: {len(moved)}행")
        for row in moved:
            backup.append({"action": "split", **_backup_row(row)})
            ops.append(UpdateOne({"_id": row["_id"]}, {"$set": split_update(row, split["new_doc_id"])}))
    for target in find_versions_to_restore(rows, today):
        restored = rows_to_restore(rows, target)
        stat["restores"] += 1
        stat["restore_rows"] += len(restored)
        out(f"  되살리기 {target['doc_id']} 시행 {target['effective']}: {len(restored)}행")
        for row in restored:
            backup.append({"action": "restore", **_backup_row(row)})
            ops.append(UpdateOne({"_id": row["_id"]}, {"$set": {"metadata.is_active": True},
                                                       "$unset": {"metadata.update_summary": ""}}))

    if args.apply and ops:
        # 되돌릴 기록을 먼저 남긴다. 쓸 것이 없으면 기존 기록을 건드리지 않는다.
        Path(args.backup).write_text(json.dumps(backup, ensure_ascii=False, default=str), encoding="utf-8")
        for start in range(0, len(ops), BATCH):
            stat["written"] += collection.bulk_write(ops[start:start + BATCH], ordered=False).modified_count
    out(f"{'적용' if args.apply else '미리 보기'}: {stat}")
    return stat


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="운영 DB 에 쓴다(없으면 미리 보기)")
    parser.add_argument("--backup", help="바꾸기 전 값을 적을 새 파일(되돌릴 때 쓴다)")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply 에는 --backup 이 필요하다")
    if args.apply and Path(args.backup).exists():
        parser.error("--backup 파일이 이미 있다")

    from scripts.core.database.mongo_client import get_mongo_db

    run(args, get_mongo_db())


if __name__ == "__main__":
    main()
