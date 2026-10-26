"""저장소가 만드는 인덱스: 텍스트 인덱스($text)는 만들지 않는다.

법령 검색은 Atlas Search(legal_document_search)·벡터 검색을 쓰고 $text 질의는 어디에도 없다.
law 컬렉션의 title·content·sub_title 텍스트 인덱스는 660MB로 데이터보다 컸고 사용 횟수 0이었다(2026-10-26 삭제).
"""
from scripts.core.database.unified_repository import UnifiedDocumentRepository


class _Collection:
    def __init__(self):
        self.created = []

    def create_index(self, keys, **kwargs):
        self.created.append(list(keys))


class _DB(dict):
    def __missing__(self, name):
        self[name] = _Collection()
        return self[name]


def test_repository_creates_no_text_index():
    db = _DB()
    UnifiedDocumentRepository(db, collection_name="law")
    created = db["law"].created
    assert created, "다른 인덱스는 그대로 만든다"
    assert not any(direction == "text" for keys in created for _, direction in keys)
