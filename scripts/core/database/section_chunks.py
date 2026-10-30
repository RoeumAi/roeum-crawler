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

    total = count_tokens(content)
    if total <= MAX_TOKENS:
        return [(content, total)]
    parts = chunk_text_by_tokens(content, max_tokens=MAX_TOKENS, overlap_tokens=OVERLAP_TOKENS)
    return [(part, count_tokens(part)) for part in parts]


def mark_doc_save_incomplete(collection, doc_id: str) -> None:
    """일부 섹션만 저장된 문서를 표시한다. 표시가 있으면 update 모드가 doc_id 가 있어도 다시 수집한다.

    저장에 성공한 섹션은 `$unset` 으로 표시를 지우므로(저장 쓰기에 함께 실린다) 전부 저장된 문서는 표시가 남지 않는다.
    """
    try:
        collection.update_many({"doc_id": doc_id, "metadata.is_active": {"$ne": False}},
                               {"$set": {"metadata.save_incomplete": True}})
    except Exception as e:  # noqa: BLE001 - 표시 실패는 저장 실패 보고에 이미 포함된다
        logger.error(f"{doc_id}: 저장 불완전 표시 실패: {e}")


def save_section_chunks(collection_name: str, doc_id: str, doc_title: str, doc_subtitle: str,
                        url: str, chunks: list) -> bool:
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        collection = get_mongo_db()[collection_name]
        saved = 0
        failed = 0
        current_keys = set()
        # 같은 제목의 섹션(【이유】 두 개 등)이 같은 키로 덮어쓰지 않도록 제목별로 chunk_seq 를 이어서 매긴다.
        # 제목이 겹치지 않는 문서는 예전과 같이 1부터 시작한다.
        seq_used: dict[str, int] = {}

        for chunk_idx, chunk in enumerate(chunks, 1):
            chunk_title = chunk.get('title', '')
            try:
                parts = split_section(chunk.get('text', ''))
            except Exception as e:  # noqa: BLE001 - 나누지 못한 섹션은 저장하지 않고 실패로 센다
                logger.error(f"{doc_id} {chunk_title}: 토큰 분할 실패: {e}")
                failed += 1
                continue

            seq_base = seq_used.get(chunk_title, 0)
            seq_used[chunk_title] = seq_base + len(parts)
            for part_no, (sub_content, token_cnt) in enumerate(parts, 1):
                seq = seq_base + part_no
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
                            "$unset": {"metadata.save_incomplete": ""},
                        },
                        upsert=True,
                    )
                    saved += 1
                    current_keys.add((chunk_title, seq))
                except Exception as e:
                    logger.error(f"청크 저장 실패 {chunk_title}: {e}")
                    failed += 1

        logger.info(f"✅ {doc_id}: {saved}개 저장 (실패: {failed})")
        if failed:
            mark_doc_save_incomplete(collection, doc_id)
        if failed == 0 and current_keys:
            # 이번에 없는 섹션(예: 예전에 남긴 메타데이터 청크)은 끈다. 판례 저장과 같은 규칙.
            for row in collection.find({"doc_id": doc_id, "metadata.is_active": True},
                                       {"doc_type": 1, "chunk_seq": 1}):
                if (row.get("doc_type"), row.get("chunk_seq")) not in current_keys:
                    collection.update_one({"_id": row["_id"]}, {"$set": {
                        "metadata.is_active": False,
                        "metadata.deactivated_at": datetime.now().isoformat(),
                    }})
        return is_complete_chunk_save(saved=saved, failed=failed)
    except Exception as e:
        logger.error(f"MongoDB 저장 실패: {e}", exc_info=True)
        return False
