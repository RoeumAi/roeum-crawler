"""섹션(청크) 단위 문서 저장 — 판례·헌재결정례·법제처해석례·행정심판재결례가 같이 쓴다.

섹션마다 8000토큰이 넘으면 나눠서 `(doc_id, doc_type, chunk_seq)` 기준으로 upsert 한다.
토크나이저를 못 쓰면 실패로 돌려준다. 예전에는 나누지 않은 채 저장해 임베딩 한도를 넘는
청크가 조용히 들어갈 수 있었다.
"""
from datetime import datetime

from scripts.core.database.source_versioning import (
    build_mongo_set_fields,
    enrich_source_document,
    is_complete_chunk_save,
    sha256_content,
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


def _chunk_key_filter(doc_id: str, chunk_title: str, seq: int) -> dict:
    return {"doc_id": doc_id, "doc_type": chunk_title, "chunk_seq": seq}


def save_section_chunks(collection_name: str, doc_id: str, doc_title: str, doc_subtitle: str,
                        url: str, chunks: list, *, hash_skip: bool = False,
                        extra_chunk_meta: dict | None = None,
                        include_unflagged_rows: bool = False) -> bool:
    """섹션별로 upsert 한다. 옵션을 주지 않으면 헌재·법제처·행정심판이 쓰던 동작 그대로다.

    hash_skip: 저장된 행의 content_hash 가 같으면 본문은 다시 쓰지 않고 제목·부제·last_check_at 만 갱신한다(판례).
    extra_chunk_meta: 모든 행의 metadata 에 더할 값(판례의 선고일 effective). 섹션 자체 metadata 가 우선한다.
    include_unflagged_rows: 비활성화 대상을 찾을 때 is_active 필드가 아예 없는 옛 행도 포함한다(판례).
    """
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        collection = get_mongo_db()[collection_name]
        saved = 0
        unchanged = 0
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
                key_filter = _chunk_key_filter(doc_id, chunk_title, seq)
                chunk_id = section_chunk_id(collection_name, doc_id, chunk_title, seq)
                try:
                    if hash_skip:
                        existing = collection.find_one(key_filter, {"content_hash": 1})
                        if existing and existing.get("content_hash") == sha256_content(sub_content):
                            collection.update_one(key_filter, {
                                "$set": {
                                    "chunk_id": chunk_id,
                                    "title": doc_title,
                                    "sub_title": doc_subtitle,
                                    "article_number": str(seq),
                                    "article_title": chunk_title,
                                    "metadata.last_check_at": datetime.now().isoformat(),
                                    "metadata.is_active": True,
                                },
                                "$unset": {"metadata.save_incomplete": ""},
                            })
                            unchanged += 1
                            current_keys.add((chunk_title, seq))
                            continue

                    chunk_meta = {
                        "source_url": url,
                        "source_type": "web",
                        "updated_at": datetime.now().isoformat(),
                        "is_active": True,
                        "chunk_index": chunk_idx,
                        "total_chunks": len(chunks),
                        "total_sub_chunks": len(parts),
                        "token_count": token_cnt,
                        **(extra_chunk_meta or {}),
                        **chunk.get("metadata", {}),
                    }
                    chunk_doc = {
                        "chunk_id": chunk_id,
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

                    collection.update_one(
                        key_filter,
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

        if hash_skip:
            logger.info(f"✅ {doc_id}: {saved}개 저장, {unchanged}개 변경없음 (실패: {failed})")
        else:
            logger.info(f"✅ {doc_id}: {saved}개 저장 (실패: {failed})")
        if failed:
            mark_doc_save_incomplete(collection, doc_id)
        if failed == 0 and current_keys:
            # 이번에 없는 섹션(예: 예전에 남긴 메타데이터 청크)은 끈다. 판례 저장과 같은 규칙.
            active_filter = {"$ne": False} if include_unflagged_rows else True
            for row in collection.find({"doc_id": doc_id, "metadata.is_active": active_filter},
                                       {"doc_type": 1, "chunk_seq": 1}):
                if (row.get("doc_type"), row.get("chunk_seq")) not in current_keys:
                    selector = {"_id": row["_id"]} if "_id" in row else _chunk_key_filter(
                        doc_id, row.get("doc_type"), row.get("chunk_seq"))
                    collection.update_one(selector, {"$set": {
                        "metadata.is_active": False,
                        "metadata.deactivated_at": datetime.now().isoformat(),
                    }})
        return is_complete_chunk_save(saved=saved, unchanged=unchanged, failed=failed)
    except Exception as e:
        logger.error(f"MongoDB 저장 실패: {e}", exc_info=True)
        return False
