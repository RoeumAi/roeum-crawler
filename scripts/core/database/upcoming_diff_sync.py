"""
시행예정(law/adrule) 버전의 신구대조표(update_summary)를 크롤링 시점에 미리 생성한다.

기존에는 refresh_current_status_flow가 시행예정 버전을 현행으로 승격시키는 순간에만
diff를 만들었기 때문에, 시행일이 오기 전까지는 "무엇이 바뀌는지"를 조회할 수 없었다.
이 모듈은 active 버전들을 effective 순으로 정렬해, 시행예정 버전마다 직전 버전과의
조문 diff를 metadata.update_summary로 기록한다. 여러 번 실행해도 같은 결과가 되는
idempotent 동작이므로 크롤링 직후 hook과 기존 데이터 백필에 동일하게 쓴다.
"""

import os
import sys
from typing import Dict, List, Tuple

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.core.database.change_detector import ArticleDiffBuilder
from scripts.core.database.refresh_promotion import group_active_documents_by_stable_id


def plan_upcoming_diff_pairs(doc_reps: Dict[str, Dict]) -> List[Tuple[str, str]]:
    """
    한 그룹(같은 law_id/adrule_id)의 버전들을 effective 순으로 정렬해,
    시행예정 버전마다 (직전 버전 doc_id, 시행예정 doc_id) 쌍을 반환한다.

    doc_reps: {doc_id: {"effective": str, "is_upcoming": bool, "created_at": str}}
    """
    ordered = sorted(
        doc_reps.items(),
        key=lambda item: (item[1].get("effective", ""), item[1].get("created_at", "")),
    )
    pairs = []
    for (prev_id, _), (next_id, next_rep) in zip(ordered, ordered[1:]):
        if next_rep.get("is_upcoming"):
            pairs.append((prev_id, next_id))
    return pairs


def sync_group_upcoming_diffs(collection, doc_reps: Dict[str, Dict]) -> int:
    """
    한 그룹의 시행예정 버전들에 update_summary를 기록하고,
    더 이상 유효하지 않은 update_summary는 제거한다.

    Returns: 기록된 diff 조문 수
    """
    diff_count = 0
    for prev_id, next_id in plan_upcoming_diff_pairs(doc_reps):
        old_articles = list(collection.find(
            {"doc_id": prev_id}, {"article_number": 1, "content": 1}
        ))
        new_articles = list(collection.find(
            {"doc_id": next_id}, {"article_number": 1, "content": 1}
        ))
        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        new_article_numbers = {a["article_number"] for a in new_articles}
        written = []
        for diff in diffs:
            if diff["article_number"] in new_article_numbers:
                collection.update_one(
                    {"doc_id": next_id, "article_number": diff["article_number"]},
                    {"$set": {"metadata.update_summary": diff}},
                )
                written.append(diff["article_number"])

        collection.update_many(
            {"doc_id": next_id, "article_number": {"$nin": written},
             "metadata.update_summary": {"$exists": True}},
            {"$unset": {"metadata.update_summary": ""}},
        )
        diff_count += len(written)
    return diff_count


def sync_collection_upcoming_diffs(collection, id_field: str) -> Dict:
    """
    단일 컬렉션(law 또는 adrule)의 모든 그룹에 대해 시행예정 diff를 동기화한다.

    Returns: {"groups_checked": int, "groups_with_upcoming": int, "diff_articles": int}
    """
    active_docs = list(collection.find(
        {"metadata.is_active": True},
        {"doc_id": 1, f"metadata.{id_field}": 1, "metadata.effective": 1,
         "metadata.is_upcoming": 1, "metadata.created_at": 1, "metadata.updated_at": 1},
    ))
    groups = group_active_documents_by_stable_id(active_docs, id_field)

    summary = {"groups_checked": 0, "groups_with_upcoming": 0, "diff_articles": 0}
    for doc_reps in groups.values():
        summary["groups_checked"] += 1
        if not plan_upcoming_diff_pairs(doc_reps):
            continue
        summary["groups_with_upcoming"] += 1
        summary["diff_articles"] += sync_group_upcoming_diffs(collection, doc_reps)
    return summary


# 신구대조표 버전 추적을 지원하는 스크래퍼와 그룹핑 기준 필드
UPCOMING_DIFF_ID_FIELDS = {"law": "law_id", "adrule": "adrule_id"}


def sync_scraper_upcoming_diffs(db, scraper_type: str) -> Dict:
    """
    크롤링 flow에서 호출하는 진입점. law/adrule 외 스크래퍼는 건너뛴다.
    """
    id_field = UPCOMING_DIFF_ID_FIELDS.get(scraper_type)
    if not id_field:
        return {"status": "skipped", "scraper_type": scraper_type}

    summary = sync_collection_upcoming_diffs(db[scraper_type], id_field)
    return {"status": "success", "scraper_type": scraper_type, **summary}


if __name__ == "__main__":
    # 기존 데이터 백필용 standalone 실행: law/adrule 전체 동기화
    from scripts.core.database.mongo_client import get_mongo_db

    db = get_mongo_db()
    for collection_name, id_field in (("law", "law_id"), ("adrule", "adrule_id")):
        result = sync_collection_upcoming_diffs(db[collection_name], id_field)
        print(f"[{collection_name}] {result}")
