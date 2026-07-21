"""
law/adrule 일일 재계산(refresh)에 필요한 순수 로직.

MongoDB I/O 없이 문서 메타데이터만으로 그룹핑/승격 판단을 수행한다.
실제 DB 반영은 scripts/core/flows/refresh_current_status_flow.py가 담당한다.
"""

from typing import Dict, List


def group_active_documents_by_stable_id(documents: List[Dict], id_field: str) -> Dict[str, Dict[str, Dict]]:
    """
    is_active=True 문서 목록을 metadata.<id_field> 기준으로 그룹핑한다.

    Returns:
        {group_id: {doc_id: {"effective": str, "is_upcoming": bool, "created_at": str}}}
    """
    groups: Dict[str, Dict[str, Dict]] = {}
    for doc in documents:
        doc_id = doc.get("doc_id")
        metadata = doc.get("metadata") or {}
        group_id = metadata.get(id_field)
        if not doc_id or not group_id:
            continue
        group = groups.setdefault(group_id, {})
        group.setdefault(doc_id, {
            "effective": metadata.get("effective") or "",
            "is_upcoming": bool(metadata.get("is_upcoming", False)),
            "created_at": metadata.get("created_at") or "",
        })
    return groups


def plan_group_transitions(doc_reps: Dict[str, Dict], today: str) -> Dict:
    """
    한 그룹(같은 law_id/adrule_id) 안에서 어떤 doc_id를 현재 버전으로 승격하고
    어떤 doc_id를 비활성화할지 결정한다.

    doc_reps: {doc_id: {"effective": str, "is_upcoming": bool, "created_at": str}}
    today: "YYYY-MM-DD"
    """
    current_candidates = {
        doc_id: rep for doc_id, rep in doc_reps.items()
        if rep.get("effective") and rep["effective"] <= today
    }
    upcoming_candidates = {
        doc_id: rep for doc_id, rep in doc_reps.items()
        if doc_id not in current_candidates
    }

    new_current = None
    if current_candidates:
        new_current = max(
            current_candidates,
            key=lambda doc_id: (
                current_candidates[doc_id]["effective"],
                current_candidates[doc_id].get("created_at", ""),
            ),
        )

    old_current = next(
        (doc_id for doc_id, rep in doc_reps.items() if rep.get("is_upcoming") is False),
        None,
    )

    promoted = bool(new_current) and new_current != old_current

    deactivate = set()
    for doc_id in current_candidates:
        if doc_id != new_current:
            deactivate.add(doc_id)
    if promoted and old_current and old_current != new_current:
        deactivate.add(old_current)

    by_effective: Dict[str, List[str]] = {}
    for doc_id, rep in upcoming_candidates.items():
        by_effective.setdefault(rep.get("effective", ""), []).append(doc_id)

    keep_upcoming = set()
    for effective, doc_ids in by_effective.items():
        if len(doc_ids) == 1:
            keep_upcoming.add(doc_ids[0])
            continue
        latest = max(doc_ids, key=lambda d: upcoming_candidates[d].get("created_at", ""))
        keep_upcoming.add(latest)
        for doc_id in doc_ids:
            if doc_id != latest:
                deactivate.add(doc_id)

    return {
        "new_current": new_current,
        "old_current": old_current,
        "promoted": promoted,
        "activate_current": new_current if promoted else None,
        "deactivate": sorted(deactivate),
        "keep_upcoming": sorted(keep_upcoming),
    }
