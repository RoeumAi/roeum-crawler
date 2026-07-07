"""Canonical identifiers for legal document chunks."""

from __future__ import annotations

import re
from typing import Any


def normalize_article_number(value: Any, fallback: str = "1") -> str:
    """Return a stable string article number."""
    if value is None or value == "":
        return fallback
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def slug_component(value: Any, fallback: str = "chunk") -> str:
    """Make a value safe enough for use inside a chunk_id."""
    text = str(value or "").strip() or fallback
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[:/\\?#&]+", "_", text)
    return text


def article_chunk_id(collection: str, doc_id: Any, article_number: Any) -> str:
    article = normalize_article_number(article_number)
    return f"{collection}:{doc_id}:article:{article}"


def section_chunk_id(collection: str, doc_id: Any, doc_type: Any, chunk_seq: Any) -> str:
    section = slug_component(doc_type, "section")
    seq = normalize_article_number(chunk_seq)
    return f"{collection}:{doc_id}:{section}:{seq}"


def document_chunk_id(collection: str, doc_id: Any) -> str:
    return f"{collection}:{doc_id}"
