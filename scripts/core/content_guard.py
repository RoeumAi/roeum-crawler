"""
Content length guard for embedding compatibility.

Uses the actual tokenizer from the embedding model (kakao1513/KURE-legal-ft-v1)
to enforce the 8,192 token context limit. Applied before every MongoDB save
so all documents in original_db are embedding-ready.
"""

import logging
from transformers import AutoTokenizer

logger = logging.getLogger(__name__)

MODEL_NAME = "kakao1513/KURE-legal-ft-v1"
MAX_TOKENS = 8192

# Load once at module level — tokenizer is lightweight
_tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)


def truncate_to_max_tokens(text: str, max_tokens: int = MAX_TOKENS) -> str:
    """Truncate text to fit within max_tokens using the actual tokenizer."""
    if not text:
        return ""
    enc = _tokenizer(
        text,
        truncation=True,
        max_length=max_tokens,
        add_special_tokens=False,
        return_attention_mask=False,
        return_token_type_ids=False,
    )
    return _tokenizer.decode(enc["input_ids"], skip_special_tokens=True)


def apply_content_guard(doc: dict, max_tokens: int = MAX_TOKENS) -> dict:
    """Truncate the content field if it exceeds max_tokens.

    Adds metadata flags for traceability.
    """
    content = doc.get("content")
    if not content or not isinstance(content, str):
        return doc

    token_count = len(_tokenizer.encode(content, add_special_tokens=False))
    if token_count <= max_tokens:
        return doc

    original_len = len(content)
    original_tokens = token_count

    doc["content"] = truncate_to_max_tokens(content, max_tokens)

    if "metadata" not in doc:
        doc["metadata"] = {}
    doc["metadata"]["content_truncated"] = True
    doc["metadata"]["original_content_length"] = original_len
    doc["metadata"]["original_token_count"] = original_tokens

    logger.debug(
        f"[content_guard] doc_id={doc.get('doc_id', '?')} "
        f"truncated {original_tokens} → {max_tokens} tokens"
    )
    return doc
