"""Backfill canonical ``sub_title`` values used by the web reference panel.

Read-only by default. Pass ``--apply`` to update MongoDB.
"""

from __future__ import annotations

import argparse
from collections import Counter

from scripts.core.database.mongo_client import get_mongo_db
from scripts.utils.reference_sub_title import (
    extract_court_reference,
    extract_nlrc_case_number,
    legacy_pdf_case_sub_title,
)


EXTRACTORS = {
    "decision": extract_court_reference,
    "mediation_case": extract_nlrc_case_number,
    "judgment": extract_nlrc_case_number,
}


def migrate_collection(collection, name: str, apply: bool) -> Counter:
    stats = Counter()
    representatives = {}
    for document in collection.find(
        {"metadata.is_active": True},
        {"doc_id": 1, "title": 1, "sub_title": 1},
    ):
        representatives.setdefault(str(document.get("doc_id")), document)

    extractor = EXTRACTORS[name]
    for doc_id, document in representatives.items():
        expected = extractor(document.get("title") or "")
        current = str(document.get("sub_title") or "")
        stats["documents"] += 1
        stats["numbered" if expected else "without_number"] += 1
        if current == expected:
            stats["unchanged"] += 1
            continue
        stats["would_update"] += collection.count_documents({"doc_id": doc_id})
        if apply:
            result = collection.update_many(
                {"doc_id": doc_id},
                {"$set": {"sub_title": expected}, "$unset": {"subtitle": ""}},
            )
            stats["updated"] += result.modified_count
    return stats


def migrate_legacy_pdf_cases(collection, apply: bool) -> Counter:
    stats = Counter()
    representatives = {}
    for document in collection.find(
        {
            "metadata.is_active": True,
            "$or": [
                {"sub_title": {"$exists": False}},
                {"sub_title": ""},
            ],
        },
        {"doc_id": 1, "title": 1},
    ):
        representatives.setdefault(str(document.get("doc_id")), document)

    for doc_id, document in representatives.items():
        expected = legacy_pdf_case_sub_title(document.get("title") or "")
        stats["documents"] += 1
        if not expected:
            stats["without_number"] += 1
            continue
        stats["numbered"] += 1
        stats["would_update"] += collection.count_documents({"doc_id": doc_id})
        if apply:
            result = collection.update_many(
                {"doc_id": doc_id},
                {"$set": {"sub_title": expected}, "$unset": {"subtitle": ""}},
            )
            stats["updated"] += result.modified_count

    if apply:
        collection.update_many({}, {"$unset": {"subtitle": ""}})
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = get_mongo_db()
    mode = "apply" if args.apply else "dry-run"
    for name in ("decision", "mediation_case", "judgment"):
        print(name, mode, dict(migrate_collection(db[name], name, args.apply)))
    print("case", mode, dict(migrate_legacy_pdf_cases(db["case"], args.apply)))

    if args.apply:
        for name in ("legislation_expc", "admin_decc", "constitutional_decc"):
            result = db[name].update_many({}, {"$unset": {"subtitle": ""}})
            print(name, "removed_legacy_subtitle", result.modified_count)


if __name__ == "__main__":
    main()
