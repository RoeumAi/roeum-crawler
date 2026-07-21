"""Shared immutable provenance fields for legal source chunks."""

from __future__ import annotations

import copy
import hashlib
from typing import Any


def sha256_content(content: Any) -> str:
    normalized = str(content or "")
    if not normalized.strip():
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_source_version_id(collection: str, chunk_id: str, content_hash: str) -> str:
    parts = [str(value or "").strip() for value in (collection, chunk_id, content_hash)]
    return f"{parts[0]}:{parts[1]}:{parts[2]}" if all(parts) else ""


def enrich_source_document(document: dict, collection: str) -> dict:
    enriched = copy.deepcopy(document)
    content_hash = sha256_content(enriched.get("content"))
    chunk_id = str(enriched.get("chunk_id") or enriched.get("doc_id") or "").strip()
    metadata = dict(enriched.get("metadata") or {})
    metadata.setdefault("is_active", True)

    source_version_id = build_source_version_id(
        collection,
        chunk_id,
        content_hash,
    )
    if source_version_id:
        enriched["content_hash"] = content_hash
        enriched["source_version_id"] = source_version_id
    else:
        enriched.pop("content_hash", None)
        enriched.pop("source_version_id", None)
    enriched["metadata"] = metadata
    return enriched
