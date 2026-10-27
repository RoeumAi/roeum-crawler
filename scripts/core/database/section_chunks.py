"""섹션(청크) 단위 문서 저장 — 헌재결정례·법제처해석례·행정심판재결례가 같이 쓴다.

섹션마다 8000토큰이 넘으면 나눠서 `(doc_id, doc_type, chunk_seq)` 기준으로 upsert 한다.
토크나이저를 못 쓰면 실패로 돌려준다. 예전에는 나누지 않은 채 저장해 임베딩 한도를 넘는
청크가 조용히 들어갈 수 있었다.
"""
from datetime import datetime

from scripts.core.database.source_versioning import (
    build_mongo_set_fields,
    enrich_source_document,
    is_complete_chunk_save,
)
from scripts.core.identifiers import section_chunk_id
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__)

MAX_TOKENS = 8000
OVERLAP_TOKENS = 200


def split_section(content: str) -> list[tuple[str, int]]:
    """섹션 본문을 [(조각, 토큰 수)] 로. 토크나이저 오류는 그대로 올린다."""
    from scripts.utils.tokenizer_utils import chunk_text_by_tokens, count_tokens

    if count_tokens(content) > MAX_TOKENS:
        parts = chunk_text_by_tokens(content, max_tokens=MAX_TOKENS, overlap_tokens=OVERLAP_TOKENS)
    else:
        parts = [content]
    return [(part, count_tokens(part)) for part in parts]


def save_section_chunks(collection_name: str, doc_id: str, doc_title: str, doc_subtitle: str,
                        url: str, chunks: list) -> bool:
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        collection = get_mongo_db()[collection_name]
        saved = 0
        failed = 0

        for chunk_idx, chunk in enumerate(chunks, 1):
            chunk_title = chunk.get('title', '')
            try:
                parts = split_section(chunk.get('text', ''))
            except Exception as e:  # noqa: BLE001 - 나누지 못한 섹션은 저장하지 않고 실패로 센다
                logger.error(f"{doc_id} {chunk_title}: 토큰 분할 실패: {e}")
                failed += 1
                continue

            for seq, (sub_content, token_cnt) in enumerate(parts, 1):
                chunk_meta = {
                    "source_url": url,
                    "source_type": "web",
                    "updated_at": datetime.now().isoformat(),
                    "is_active": True,
                    "chunk_index": chunk_idx,
                    "total_chunks": len(chunks),
                    "total_sub_chunks": len(parts),
                    "token_count": token_cnt,
                    **chunk.get("metadata", {}),
                }
                chunk_doc = {
                    "chunk_id": section_chunk_id(collection_name, doc_id, chunk_title, seq),
                    "doc_id": doc_id,
                    "doc_type": chunk_title,
                    "title": doc_title,
                    "sub_title": doc_subtitle,
                    "article_number": str(seq),
                    "article_title": chunk_title,
                    "chunk_seq": seq,
                    "content": sub_content,
                }
                chunk_doc = enrich_source_document(chunk_doc, collection_name)
                set_fields = build_mongo_set_fields(chunk_doc, chunk_meta)

                try:
                    collection.update_one(
                        {"doc_id": doc_id, "doc_type": chunk_title, "chunk_seq": seq},
                        {
                            "$set": set_fields,
                            "$setOnInsert": {"metadata.created_at": datetime.now().isoformat()},
                        },
                        upsert=True,
                    )
                    saved += 1
                except Exception as e:
                    logger.error(f"청크 저장 실패 {chunk_title}: {e}")
                    failed += 1

        logger.info(f"✅ {doc_id}: {saved}개 저장 (실패: {failed})")
        return is_complete_chunk_save(saved=saved, failed=failed)
    except Exception as e:
        logger.error(f"MongoDB 저장 실패: {e}", exc_info=True)
        return False
