"""
"2027년 적용 최저임금 고시" 재크롤링 루프가 남긴 데이터 정리 (멱등, 재실행 가능).

- doc_id 2100000283564 (확정 고시): 중복 청크를 최초 1개만 남기고 삭제,
  created_at 백필, is_active=True / is_upcoming=True로 교정
- doc_id 2100000282670 (구 초안 "최저임금안 고시"): is_active=False로 비활성화

사용법:
    python3 scripts/maintenance/fix_minwage_recrawl_loop.py           # dry-run (조회만)
    python3 scripts/maintenance/fix_minwage_recrawl_loop.py --apply  # 실제 반영
"""

import os
import sys
from datetime import datetime

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

CONFIRMED_DOC_ID = "2100000283564"  # 2027년 적용 최저임금 고시 (확정)
DRAFT_DOC_ID = "2100000282670"      # 2027년 적용 최저임금안 고시 (초안)
CONFIRMED_CREATED_AT = "2026-08-07"  # 확정 고시 최초 크롤링일 (KST)


def run(apply: bool) -> None:
    from scripts.core.database.mongo_client import get_mongo_db

    col = get_mongo_db()["adrule"]
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] original_db.adrule 정리 시작")

    # 1. 확정 고시의 중복 청크 정리: 최초 삽입(_id 최소)만 남긴다
    chunks = list(col.find({"doc_id": CONFIRMED_DOC_ID}, {"_id": 1}).sort("_id", 1))
    if not chunks:
        print(f"  ⚠️ {CONFIRMED_DOC_ID} 문서가 없습니다. 중단.")
        return
    keep_id = chunks[0]["_id"]
    dup_ids = [c["_id"] for c in chunks[1:]]
    print(f"  확정 고시 청크 {len(chunks)}개 → 유지 {keep_id}, 삭제 대상 {len(dup_ids)}개")
    if apply and dup_ids:
        result = col.delete_many({"_id": {"$in": dup_ids}})
        print(f"  🗑️ 중복 청크 {result.deleted_count}개 삭제")

    # 2. 남긴 청크의 메타데이터 교정 (created_at 백필 + 시행예정 활성화)
    now = datetime.now().isoformat()
    fix = {
        "metadata.created_at": CONFIRMED_CREATED_AT,
        "metadata.is_active": True,
        "metadata.is_upcoming": True,
        "metadata.updated_at": now,
    }
    print(f"  확정 고시 교정: {fix}")
    if apply:
        col.update_one({"_id": keep_id}, {"$set": fix})

    # 3. 구 초안 비활성화
    draft_active = col.count_documents({"doc_id": DRAFT_DOC_ID, "metadata.is_active": True})
    print(f"  초안 활성 청크 {draft_active}개 → 비활성화")
    if apply and draft_active:
        col.update_many(
            {"doc_id": DRAFT_DOC_ID, "metadata.is_active": True},
            {"$set": {"metadata.is_active": False, "metadata.updated_at": now}},
        )

    # 4. 결과 확인
    print("  --- 정리 후 상태 ---")
    for doc_id in (CONFIRMED_DOC_ID, DRAFT_DOC_ID):
        for d in col.find({"doc_id": doc_id},
                          {"metadata.is_active": 1, "metadata.is_upcoming": 1,
                           "metadata.created_at": 1, "metadata.effective": 1}):
            m = d.get("metadata", {})
            print(f"  {doc_id}: active={m.get('is_active')} upcoming={m.get('is_upcoming')} "
                  f"created={m.get('created_at')} eff={m.get('effective')}")
    print(f"[{mode}] 완료")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
