"""Conservative rejection of records with no legal body; no length threshold."""
import re
import unicodedata


def has_legal_body(document: dict) -> bool:
    metadata = document.get("metadata") or {}
    if metadata.get("is_stub") or metadata.get("is_metadata_only"):
        return False
    if document.get("doc_type") == "메타데이터":
        return False
    clean = lambda value: unicodedata.normalize("NFC", str(value or "").strip())
    content = clean(document.get("content"))
    title = clean(document.get("title") or metadata.get("title"))
    if not content or (title and content == title):
        return False
    return any(
        line.strip() and not re.match(r"^\s*(?:제목\s*[:：]|출처\s*[:：]\s*https?://)", line)
        for line in content.splitlines()
    )
