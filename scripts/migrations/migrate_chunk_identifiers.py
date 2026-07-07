"""Backfill canonical chunk identifiers across legal collections.

This migration is intentionally non-destructive by default. It only sets
identifier/display fields that the retriever and web citation panel need.
Potential collection contamination is reported separately and should be cleaned
up only after reviewing samples.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable

from pymongo import ASCENDING, UpdateOne
from pymongo.errors import OperationFailure

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.core.database.mongo_client import get_mongo_db
from scripts.core.identifiers import (
    article_chunk_id,
    document_chunk_id,
    normalize_article_number,
    section_chunk_id,
)

BATCH_SIZE = 500


def flush_ops(collection, ops: list[UpdateOne], dry_run: bool) -> int:
    if not ops:
        return 0
    if dry_run:
        return len(ops)
    result = collection.bulk_write(ops, ordered=False)
    return result.modified_count + result.upserted_count


def iter_update(
    collection_name: str,
    projection: dict[str, int],
    build_update: Callable[[dict[str, Any]], dict[str, Any] | None],
    dry_run: bool,
) -> int:
    db = get_mongo_db()
    collection = db[collection_name]
    ops: list[UpdateOne] = []
    affected = 0

    cursor = collection.find({}, projection, no_cursor_timeout=True).batch_size(2000)
    try:
        for doc in cursor:
            update = build_update(doc)
            if not update:
                continue
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": update}))
            if len(ops) >= BATCH_SIZE:
                affected += flush_ops(collection, ops, dry_run)
                ops.clear()
        affected += flush_ops(collection, ops, dry_run)
    finally:
        cursor.close()

    return affected


def backfill_article_collection(
    collection_name: str,
    dept_name: str,
    fallback_article: Callable[[dict[str, Any]], Any],
    dry_run: bool,
) -> int:
    def build(doc: dict[str, Any]) -> dict[str, Any]:
        metadata = doc.get("metadata") or {}
        article_number = normalize_article_number(
            doc.get("article_number"),
            fallback=normalize_article_number(fallback_article(doc)),
        )
        update = {
            "chunk_id": article_chunk_id(collection_name, doc.get("doc_id"), article_number),
            "article_number": article_number,
            "metadata.dept_name": metadata.get("dept_name") or dept_name,
        }
        if collection_name == "law":
            update["doc_type"] = doc.get("doc_type") or "법령"
        return update

    return iter_update(
        collection_name,
        {
            "_id": 1,
            "doc_id": 1,
            "doc_type": 1,
            "article_number": 1,
            "metadata.article_index": 1,
            "metadata.dept_name": 1,
        },
        build,
        dry_run,
    )


def backfill_section_collection(collection_name: str, dept_name: str, dry_run: bool) -> int:
    def build(doc: dict[str, Any]) -> dict[str, Any]:
        metadata = doc.get("metadata") or {}
        chunk_seq = doc.get("chunk_seq") or metadata.get("chunk_index") or 1
        article_number = normalize_article_number(chunk_seq)
        doc_type = doc.get("doc_type") or "section"
        update = {
            "chunk_id": section_chunk_id(collection_name, doc.get("doc_id"), doc_type, chunk_seq),
            "article_number": article_number,
            "article_title": doc_type,
            "metadata.dept_name": metadata.get("dept_name") or dept_name,
        }
        if doc.get("subtitle") and not doc.get("sub_title"):
            update["sub_title"] = doc["subtitle"]
        return update

    return iter_update(
        collection_name,
        {
            "_id": 1,
            "doc_id": 1,
            "doc_type": 1,
            "subtitle": 1,
            "sub_title": 1,
            "chunk_seq": 1,
            "metadata.chunk_index": 1,
            "metadata.dept_name": 1,
        },
        build,
        dry_run,
    )


def backfill_document_collection(collection_name: str, dept_name: str, dry_run: bool) -> int:
    def build(doc: dict[str, Any]) -> dict[str, Any]:
        metadata = doc.get("metadata") or {}
        return {
            "chunk_id": document_chunk_id(collection_name, doc.get("doc_id")),
            "article_number": "1",
            "metadata.dept_name": metadata.get("dept_name") or dept_name,
        }

    return iter_update(
        collection_name,
        {"_id": 1, "doc_id": 1, "metadata.dept_name": 1},
        build,
        dry_run,
    )


def create_indexes(dry_run: bool) -> None:
    db = get_mongo_db()
    collections = [
        "law",
        "adrule",
        "case",
        "interpretation",
        "judgment",
        "mediation_case",
        "decision",
        "constitutional_decc",
        "legislation_expc",
        "admin_decc",
    ]
    for name in collections:
        if dry_run:
            print(f"[{name}] DRY-RUN index creation skipped")
            continue
        col = db[name]
        ensure_index(col, [("chunk_id", ASCENDING)], "idx_chunk_id")
        ensure_index(
            col,
            [
                ("doc_id", ASCENDING),
                ("article_number", ASCENDING),
                ("metadata.is_active", ASCENDING),
            ],
            "idx_doc_article_active",
        )
        ensure_index(col, [("doc_id", ASCENDING), ("chunk_seq", ASCENDING)], "idx_doc_chunk_seq")
        print(f"[{name}] indexes ensured")


def ensure_index(collection, keys: list[tuple[str, int]], name: str) -> None:
    try:
        collection.create_index(keys, name=name, background=True)
    except OperationFailure as exc:
        if exc.code != 85:
            raise
        existing = collection.index_information()
        normalized = [(field, direction) for field, direction in keys]
        for existing_name, spec in existing.items():
            if spec.get("key") == normalized:
                print(f"[{collection.name}] index {name} already exists as {existing_name}")
                return
        raise


def report() -> None:
    db = get_mongo_db()
    collections = [
        "law",
        "adrule",
        "case",
        "interpretation",
        "judgment",
        "mediation_case",
        "decision",
        "constitutional_decc",
        "legislation_expc",
        "admin_decc",
    ]
    for name in collections:
        col = db[name]
        total = col.count_documents({})
        with_chunk_id = col.count_documents({"chunk_id": {"$exists": True, "$nin": [None, ""]}})
        with_article = col.count_documents({"article_number": {"$exists": True, "$nin": [None, ""]}})
        with_dept = col.count_documents({"metadata.dept_name": {"$exists": True, "$nin": [None, ""]}})
        print(
            f"[{name}] total={total:,} chunk_id={with_chunk_id:,} "
            f"article_number={with_article:,} dept_name={with_dept:,}"
        )


def report_law_contamination(sample_limit: int = 10) -> None:
    db = get_mongo_db()
    col = db["law"]
    query = {
        "$or": [
            {"metadata.source_url": {"$regex": "precInfoP|detcInfoP|expcInfoP|deccInfoP"}},
            {"doc_type": {"$nin": ["법령", "law", None]}},
        ]
    }
    count = col.count_documents(query)
    print(f"[law] possible contamination count={count:,}")
    for doc in col.find(
        query,
        {"_id": 0, "doc_id": 1, "doc_type": 1, "title": 1, "sub_title": 1, "metadata.source_url": 1},
    ).limit(sample_limit):
        print(doc)


def run(dry_run: bool) -> None:
    steps = [
        ("law", lambda: backfill_article_collection("law", "고용노동부", lambda d: "1", dry_run)),
        (
            "adrule",
            lambda: backfill_article_collection(
                "adrule",
                "고용노동부",
                lambda d: (d.get("metadata") or {}).get("article_index") or 1,
                dry_run,
            ),
        ),
        (
            "interpretation",
            lambda: backfill_article_collection("interpretation", "고용노동부", lambda d: 1, dry_run),
        ),
        ("case", lambda: backfill_section_collection("case", "고용노동부", dry_run)),
        (
            "constitutional_decc",
            lambda: backfill_section_collection("constitutional_decc", "헌법재판소", dry_run),
        ),
        ("legislation_expc", lambda: backfill_section_collection("legislation_expc", "법제처", dry_run)),
        ("admin_decc", lambda: backfill_section_collection("admin_decc", "국민권익위원회", dry_run)),
        ("judgment", lambda: backfill_document_collection("judgment", "중앙노동위원회", dry_run)),
        ("mediation_case", lambda: backfill_document_collection("mediation_case", "중앙노동위원회", dry_run)),
        ("decision", lambda: backfill_document_collection("decision", "중앙노동위원회", dry_run)),
    ]
    for name, fn in steps:
        affected = fn()
        label = "would update" if dry_run else "updated"
        print(f"[{name}] {label} {affected:,}")
    create_indexes(dry_run)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--ensure-indexes", action="store_true")
    parser.add_argument("--report-law-contamination", action="store_true")
    args = parser.parse_args()

    if args.verify:
        report()
        return
    if args.ensure_indexes:
        create_indexes(dry_run=False)
        return
    if args.report_law_contamination:
        report_law_contamination()
        return

    run(args.dry_run)
    report()
    report_law_contamination()


if __name__ == "__main__":
    main()
