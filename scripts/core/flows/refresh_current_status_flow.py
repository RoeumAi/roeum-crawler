"""
law/adrule의 '현재 시행 버전' 매일 재계산 Prefect flow.

lawService.do로 얻은 안정 ID(law_id/adrule_id)를 기준으로 그룹핑하여,
시행예정이던 버전이 오늘 날짜로 현행이 되면 승격시키고 구 버전을 비활성화한다.
크롤러(law/adrule scraper.py)는 더 이상 이 승격/비활성화를 직접 하지 않는다.
"""

import os
import sys
from datetime import date, datetime
from typing import Dict, Optional

from prefect import flow

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.database.change_detector import ArticleDiffBuilder
from scripts.core.database.refresh_promotion import (
    group_active_documents_by_stable_id,
    plan_group_transitions,
)
from scripts.core.database.upcoming_diff_sync import sync_collection_upcoming_diffs

logger = get_logger(__name__, scraper_type='refresh_current_status')


def apply_promotion(collection, group_id: str, plan: Dict) -> None:
    """plan_group_transitions()의 결정을 실제 MongoDB에 반영한다."""
    if plan["deactivate"]:
        collection.update_many(
            {"doc_id": {"$in": plan["deactivate"]}, "metadata.is_active": True},
            {"$set": {"metadata.is_active": False, "metadata.updated_at": datetime.now().isoformat()}},
        )

    if plan["promoted"] and plan["activate_current"]:
        new_current = plan["activate_current"]
        collection.update_many(
            {"doc_id": new_current},
            {"$set": {"metadata.is_active": True, "metadata.is_upcoming": False,
                      "metadata.updated_at": datetime.now().isoformat()}},
        )

        if plan["old_current"]:
            old_articles = list(collection.find(
                {"doc_id": plan["old_current"]}, {"article_number": 1, "content": 1}
            ))
            new_articles = list(collection.find(
                {"doc_id": new_current}, {"article_number": 1, "content": 1}
            ))
            diffs = ArticleDiffBuilder.build(old_articles, new_articles)
            new_by_article = {a["article_number"]: a for a in new_articles}
            for diff in diffs:
                if diff["article_number"] in new_by_article:
                    collection.update_one(
                        {"doc_id": new_current, "article_number": diff["article_number"]},
                        {"$set": {"metadata.update_summary": diff}},
                    )
            logger.info(f"[{group_id}] 승격: {plan['old_current']} → {new_current}, 조문 변경 {len(diffs)}건")


def refresh_collection(collection, id_field: str, today: Optional[str] = None) -> Dict:
    """단일 컬렉션(law 또는 adrule)의 모든 그룹을 재계산한다."""
    today = today or date.today().isoformat()
    active_docs = list(collection.find(
        {"metadata.is_active": True},
        {"doc_id": 1, f"metadata.{id_field}": 1, "metadata.effective": 1,
         "metadata.is_upcoming": 1, "metadata.created_at": 1},
    ))
    groups = group_active_documents_by_stable_id(active_docs, id_field)

    summary = {"groups_checked": 0, "promoted": 0, "deactivated": 0}
    for group_id, doc_reps in groups.items():
        plan = plan_group_transitions(doc_reps, today)
        apply_promotion(collection, group_id, plan)
        summary["groups_checked"] += 1
        if plan["promoted"]:
            summary["promoted"] += 1
        summary["deactivated"] += len(plan["deactivate"])

    # 승격 여부와 무관하게, 시행예정 버전들의 신구대조표를 매일 최신 상태로 유지한다.
    # (run_daily.sh의 crawl.py 경로는 unified_scraper_flow의 동기화 태스크를 거치지 않는다)
    diff_summary = sync_collection_upcoming_diffs(collection, id_field)
    summary["diff_articles"] = diff_summary["diff_articles"]
    return summary


@flow(name="refresh-current-status", description="law/adrule 현재 시행 버전 일일 재계산")
def refresh_current_status_flow() -> Dict:
    from scripts.core.database.mongo_client import get_mongo_db

    db = get_mongo_db()
    today = date.today().isoformat()

    law_summary = refresh_collection(db["law"], "law_id", today)
    logger.info(f"law 재계산 완료: {law_summary}")

    adrule_summary = refresh_collection(db["adrule"], "adrule_id", today)
    logger.info(f"adrule 재계산 완료: {adrule_summary}")

    return {
        "law": law_summary,
        "adrule": adrule_summary,
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    # Prefect 서버가 없는 launchd 운영 환경에서는 오케스트레이션 없이 순수 함수로 실행
    result = refresh_current_status_flow.fn()
    print(f"\n최종 결과: {result}")
