"""법령 버전의 doc_id. 같은 공포본(lsiSeq)이 시행일을 나눠 두면 버전마다 다른 doc_id 를 준다.

doc_id·chunk_id 를 조회 키로 쓰는 곳이 여럿이다(저장 변경 감지, 승격, Pro 문서 상세, AI 출처 조회). 키를 버전마다 고유하게
만들어야 한 버전을 저장·조회할 때 다른 버전이 섞이지 않는다.
"""
from typing import Optional

NO_DATE = {"", "99991231"}


def version_doc_id(base_doc_id: str, effective: str) -> str:
    return f"{base_doc_id}-{effective.replace('-', '')}"


def resolve_version_doc_id(collection, base_doc_id: str, source_url: Optional[str], effective: Optional[str],
                           is_upcoming: bool = False) -> str:
    """이 버전을 저장할 doc_id. 같은 주소로 저장한 적이 있으면 그 doc_id.

    처음 저장하는 예정본은 늘 시행일을 붙인다. 현행본과 같은 실행에서 동시에 저장돼도 DB 조회 순서와 상관없이 겹치지 않는다.
    현행본은 다른 시행일이 이미 공포본 번호를 쓰고 있을 때만 붙인다.
    """
    if source_url:
        saved = collection.find_one({"metadata.source_url": source_url, "metadata.search_only": {"$ne": True}},
                                    {"doc_id": 1})
        if saved and saved.get("doc_id"):
            return saved["doc_id"]
    if (effective or "") in NO_DATE:
        return base_doc_id
    if is_upcoming:
        return version_doc_id(base_doc_id, effective)
    other = collection.find_one({"doc_id": base_doc_id, "metadata.effective": {"$ne": effective}}, {"_id": 1})
    return version_doc_id(base_doc_id, effective) if other else base_doc_id
