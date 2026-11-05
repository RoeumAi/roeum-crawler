"""
law/adrule 일일 재계산(refresh)에 필요한 순수 로직.

MongoDB I/O 없이 문서 메타데이터만으로 그룹핑/승격 판단을 수행한다.
실제 DB 반영은 scripts/core/flows/refresh_current_status_flow.py가 담당한다.
"""

from collections import Counter
from typing import Dict, List

# 같은 공포본이 시행일을 나눠 두면(현행 2026-07-07·예정 2027-01-08) 같은 doc_id 에 버전이 둘이다. 그때만 시행일까지 붙여 가른다.
VERSION_SEP = "@"


def version_selector(version_key: str) -> Dict:
    """그룹의 버전 키(`doc_id` 또는 `doc_id@시행일`)가 가리키는 행을 찾는 조건."""
    doc_id, _, effective = version_key.partition(VERSION_SEP)
    return {"doc_id": doc_id, "metadata.effective": effective} if effective else {"doc_id": doc_id}


def group_active_documents_by_stable_id(documents: List[Dict], id_field: str) -> Dict[str, Dict[str, Dict]]:
    """
    is_active=True 문서 목록을 metadata.<id_field> 기준으로 그룹핑한다.

    Returns:
        {group_id: {version_key: {"effective": str, "is_upcoming": bool, "created_at": str}}}
        version_key 는 보통 doc_id, 같은 doc_id 에 시행일이 둘 이상이면 `doc_id@시행일`.
    """
    versions: Dict[str, Dict[tuple, Dict]] = {}
    for doc in documents:
        doc_id = doc.get("doc_id")
        metadata = doc.get("metadata") or {}
        group_id = metadata.get(id_field)
        if not doc_id or not group_id:
            continue
        effective = metadata.get("effective") or ""
        group = versions.setdefault(group_id, {})
        group.setdefault((doc_id, effective), {
            "effective": effective,
            "is_upcoming": bool(metadata.get("is_upcoming", False)),
            # created_at이 없는 과거 버그 데이터는 updated_at(삽입 시점)으로
            # tie-break한다. ""로 두면 같은 effective 중복 정리에서 항상 진다.
            "created_at": metadata.get("created_at") or metadata.get("updated_at") or "",
        })

    groups: Dict[str, Dict[str, Dict]] = {}
    for group_id, group in versions.items():
        counts = Counter(doc_id for doc_id, _ in group)
        groups[group_id] = {
            (f"{doc_id}{VERSION_SEP}{effective}" if counts[doc_id] > 1 and effective else doc_id): rep
            for (doc_id, effective), rep in group.items()
        }
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
