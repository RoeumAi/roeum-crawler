import pytest
from scripts.utils.source_quality import has_legal_body

@pytest.mark.parametrize("doc", [
    {"content": ""},
    {"title": "판결", "content": "판결"},
    {"title": "판결", "content": "  판결  "},
    {"content": "제목: 판결\n출처: https://www.law.go.kr/1"},
    {"content": "본문", "metadata": {"is_metadata_only": True}},
    {"content": "본문", "metadata": {"is_stub": True}},
    {"content": "본문", "doc_type": "메타데이터"},
])
def test_excludes_empty_evidence(doc):
    assert not has_legal_body(doc)

@pytest.mark.parametrize("text", ["삭제", "이 법은 공포한 날부터 시행한다.", "제목: 문서\n실질 판결 본문"])
def test_keeps_short_valid_legal_body(text):
    assert has_legal_body({"title": "법령", "content": text})


def test_ingestion_flags_stub_without_mutating_input():
    from scripts.core.database.source_versioning import enrich_source_document
    original = {'title': '판례', 'content': '판례', 'chunk_id': 'case:1', 'metadata': {'is_searchable': True}}
    enriched = enrich_source_document(original, 'case')
    assert enriched['metadata']['is_stub'] is True
    assert enriched['metadata']['is_searchable'] is False
    assert original['metadata']['is_searchable'] is True
