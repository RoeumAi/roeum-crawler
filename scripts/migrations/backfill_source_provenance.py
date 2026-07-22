"""Audit or backfill citation provenance fields in original_db.

The command is read-only by default. Pass --apply to write updates and create
indexes.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.core.config import SCRAPERS
from scripts.core.database.provenance_migration import (
    audit_provenance,
    build_provenance_update,
)


def migrate_collection(collection, collection_name: str, apply: bool) -> Counter:
    stats: Counter = Counter()
    projection = {
        "_id": 1,
        "doc_id": 1,
        "chunk_id": 1,
        "content": 1,
        "content_hash": 1,
        "source_version_id": 1,
        "metadata.is_active": 1,
    }
    for document in collection.find({}, projection, no_cursor_timeout=True):
        stats["scanned"] += 1
        audit = audit_provenance(document, collection_name)
        for field, state in audit.items():
            stats[f"{field}.{state}"] += 1

        update = build_provenance_update(document, collection_name)
        if not update:
            stats["unchanged"] += 1
            continue
        stats["would_update"] += 1
        if apply:
            collection.update_one({"_id": document["_id"]}, {"$set": update})
            stats["updated"] += 1

    if apply:
        collection.create_index(
            [("chunk_id", 1), ("content_hash", 1)],
            name="idx_citation_chunk_hash",
            background=True,
        )
        collection.create_index(
            [("source_version_id", 1)],
            name="idx_source_version_id",
            background=True,
        )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--collections",
        nargs="*",
        choices=sorted({config.collection_name for config in SCRAPERS.values()}),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 MongoDB 업데이트 및 인덱스 생성을 수행합니다.",
    )
    args = parser.parse_args()

    names = args.collections or sorted(
        {config.collection_name for config in SCRAPERS.values()}
    )
    from scripts.core.database.mongo_client import get_mongo_db

    db = get_mongo_db()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"source provenance migration mode={mode}")

    for name in names:
        stats = migrate_collection(db[name], name, args.apply)
        summary = ", ".join(f"{key}={value}" for key, value in sorted(stats.items()))
        print(f"[{name}] {summary}")


if __name__ == "__main__":
    main()
