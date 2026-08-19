"""Backfill: strip PDF/print artifacts from existing MongoDB content.

Cleans documents already stored in ``original_db`` whose ``content`` carries
PDF print/page artifacts (hrdb.kr print footers, NLRC ``- N -`` page markers,
``[첨부: ...]`` filename markers) and flags title/source-only stub documents so
they stay out of the public search surface and the B2B citation panel.

When a document's content changes, ``content_hash`` and ``source_version_id``
are recomputed with the same helpers the ingest pipeline uses, so the
re-embedding job can detect the change (the stale ``embedding`` is not touched
here — re-embedding is owned by the chat_generation pipeline).

Usage:
    python3 scripts/migrations/backfill_clean_pdf_artifacts.py            # dry-run
    python3 scripts/migrations/backfill_clean_pdf_artifacts.py --apply    # write
    python3 scripts/migrations/backfill_clean_pdf_artifacts.py --collections interpretation decision
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.core.database.source_versioning import (
    build_source_version_id,
    sha256_content,
)
from scripts.utils.pdf_text_cleaner import clean_pdf_artifacts, is_stub_content

# Collections known to carry PDF-sourced artifacts (see analysis).
DEFAULT_COLLECTIONS = [
    "interpretation",
    "decision",
    "judgment",
    "case",
    "constitutional_decc",
    "admin_decc",
    "mediation_case",
    "adrule",
]


def plan_document_update(document: dict, collection: str) -> Optional[tuple[str, dict]]:
    """Plan a single document's cleanup.

    Returns ``(kind, set_fields)`` where ``kind`` is ``"clean"`` or ``"stub"``,
    or ``None`` when the document needs no change.
    """
    content = document.get("content") or ""

    if is_stub_content(content):
        metadata = document.get("metadata") or {}
        if metadata.get("is_stub") and metadata.get("is_searchable") is False:
            return None
        return "stub", {
            "metadata.is_stub": True,
            "metadata.is_searchable": False,
        }

    cleaned = clean_pdf_artifacts(content)
    if cleaned == content:
        return None

    content_hash = sha256_content(cleaned)
    chunk_id = str(document.get("chunk_id") or document.get("doc_id") or "").strip()
    return "clean", {
        "content": cleaned,
        "content_hash": content_hash,
        "source_version_id": build_source_version_id(collection, chunk_id, content_hash),
    }


def plan_collection_cleanup(documents, collection: str) -> list[tuple[object, str, dict]]:
    """Plan cleanup for an iterable of documents.

    Returns a list of ``(_id, kind, set_fields)`` for documents needing a
    change; documents that need none are omitted.
    """
    plans = []
    for document in documents:
        planned = plan_document_update(document, collection)
        if planned is None:
            continue
        kind, set_fields = planned
        plans.append((document.get("_id"), kind, set_fields))
    return plans


def run_cleanup(db, collections, apply: bool) -> dict:
    """Scan and (optionally) apply cleanup across ``collections``.

    Returns a per-collection summary dict.
    """
    summary = {}
    for name in collections:
        collection = db[name]
        cursor = collection.find(
            {},
            {"content": 1, "chunk_id": 1, "doc_id": 1, "metadata": 1},
        )
        plans = plan_collection_cleanup(cursor, name)

        cleaned = sum(1 for _, kind, _ in plans if kind == "clean")
        stubbed = sum(1 for _, kind, _ in plans if kind == "stub")

        if apply:
            for _id, _kind, set_fields in plans:
                collection.update_one({"_id": _id}, {"$set": set_fields})

        summary[name] = {"cleaned": cleaned, "stubbed": stubbed, "total": len(plans)}
        action = "applied" if apply else "dry-run"
        print(f"[{action}] {name:22s} clean={cleaned:5d}  stub={stubbed:4d}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Strip PDF/print artifacts from MongoDB content")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument(
        "--collections",
        nargs="+",
        default=DEFAULT_COLLECTIONS,
        help="collections to scan",
    )
    args = parser.parse_args()

    from scripts.core.database.mongo_client import get_mongo_db

    db = get_mongo_db()
    summary = run_cleanup(db, args.collections, apply=args.apply)

    total_clean = sum(s["cleaned"] for s in summary.values())
    total_stub = sum(s["stubbed"] for s in summary.values())
    mode = "APPLIED" if args.apply else "DRY-RUN (use --apply to write)"
    print(f"\n{mode}: {total_clean} cleaned, {total_stub} stub-flagged")


if __name__ == "__main__":
    main()
