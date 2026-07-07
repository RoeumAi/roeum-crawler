"""Prune law collection to current Ministry of Employment and Labor laws."""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

import requests
from pymongo.errors import PyMongoError

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.core.database.mongo_client import get_mongo_db

API_BASE = "https://www.law.go.kr/DRF/lawSearch.do"
OC = "inwoong100"
ORG_CODE = "1492000"
PAGE_SIZE = 100


def fetch_valid_law_ids() -> dict[str, dict]:
    first = fetch_page(1)
    total = int(first.get("totalCnt") or 0)
    items = list(first.get("law") or [])
    for page in range(2, math.ceil(total / PAGE_SIZE) + 1):
        time.sleep(0.2)
        items.extend(fetch_page(page).get("law") or [])

    valid: dict[str, dict] = {}
    for item in items:
        seq = str(item.get("법령일련번호") or "").strip()
        if not seq:
            continue
        valid[seq] = item
    return valid


def fetch_page(page: int) -> dict:
    resp = requests.get(
        API_BASE,
        params={
            "OC": OC,
            "target": "law",
            "type": "JSON",
            "org": ORG_CODE,
            "display": PAGE_SIZE,
            "page": page,
            "query": "",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("LawSearch", {})


def base_doc_id(doc_id: str) -> str:
    return str(doc_id or "").replace("_full", "")


def run(dry_run: bool, sample_limit: int) -> None:
    db = get_mongo_db()
    col = db["law"]
    valid = fetch_valid_law_ids()
    valid_ids = set(valid)
    print(f"valid ministry laws: {len(valid_ids):,}")

    all_ids = set(str(v) for v in col.distinct("doc_id") if v)
    keep_ids = {doc_id for doc_id in all_ids if base_doc_id(doc_id) in valid_ids}
    delete_ids = sorted(all_ids - keep_ids)

    print(f"law distinct doc_id before: {len(all_ids):,}")
    print(f"keep doc_id: {len(keep_ids):,}")
    print(f"delete doc_id: {len(delete_ids):,}")
    print(f"law chunks before: {col.count_documents({}):,}")
    print(f"chunks to delete: {col.count_documents({'doc_id': {'$in': delete_ids}}):,}")

    print("delete samples:")
    for doc in col.find(
        {"doc_id": {"$in": delete_ids}},
        {"_id": 0, "doc_id": 1, "title": 1, "sub_title": 1, "metadata.source_url": 1},
    ).limit(sample_limit):
        print(doc)

    if dry_run:
        print("DRY-RUN: no documents deleted")
        return

    deleted = 0
    batch_size = 25
    for i in range(0, len(delete_ids), batch_size):
        batch = delete_ids[i:i + batch_size]
        try:
            result = col.delete_many({"doc_id": {"$in": batch}})
            deleted += result.deleted_count
        except PyMongoError as exc:
            print(f"delete batch failed at {i:,}/{len(delete_ids):,}: {exc}")
            time.sleep(3)
            result = col.delete_many({"doc_id": {"$in": batch}})
            deleted += result.deleted_count
        if i == 0 or i + batch_size >= len(delete_ids) or (i // batch_size) % 10 == 0:
            print(f"deleted progress: {min(i + batch_size, len(delete_ids)):,}/{len(delete_ids):,} ids, {deleted:,} chunks")
        time.sleep(0.05)

    print(f"deleted chunks: {deleted:,}")
    print(f"law chunks after: {col.count_documents({}):,}")
    print(f"law distinct doc_id after: {len(col.distinct('doc_id')):,}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=20)
    args = parser.parse_args()
    run(args.dry_run, args.sample_limit)


if __name__ == "__main__":
    main()
