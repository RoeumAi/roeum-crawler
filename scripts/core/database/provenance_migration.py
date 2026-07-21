"""Pure planning helpers for source provenance backfills."""

from __future__ import annotations

from scripts.core.database.source_versioning import (
    build_source_version_id,
    sha256_content,
)


def expected_provenance(document: dict, collection: str) -> tuple[str, str]:
    content_hash = sha256_content(document.get("content"))
    chunk_id = str(document.get("chunk_id") or document.get("doc_id") or "").strip()
    return content_hash, build_source_version_id(collection, chunk_id, content_hash)


def build_provenance_update(document: dict, collection: str) -> dict:
    content_hash, source_version_id = expected_provenance(document, collection)
    if not content_hash or not source_version_id:
        return {}
    update: dict = {}

    if document.get("content_hash") != content_hash:
        update["content_hash"] = content_hash
    if document.get("source_version_id") != source_version_id:
        update["source_version_id"] = source_version_id
    return update


def audit_provenance(document: dict, collection: str) -> dict[str, str]:
    content_hash, source_version_id = expected_provenance(document, collection)
    metadata = document.get("metadata") or {}
    if not content_hash or not source_version_id:
        return {
            "provenance": "invalid",
            "content_hash": "invalid",
            "source_version_id": "invalid",
            "active_state": "present" if "is_active" in metadata else "missing",
        }
    return {
        "provenance": "valid",
        "content_hash": (
            "present"
            if document.get("content_hash") == content_hash
            else "missing"
            if not document.get("content_hash")
            else "mismatch"
        ),
        "source_version_id": (
            "present"
            if document.get("source_version_id") == source_version_id
            else "missing"
            if not document.get("source_version_id")
            else "mismatch"
        ),
        "active_state": "present" if "is_active" in metadata else "missing",
    }
