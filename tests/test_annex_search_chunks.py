from collections import Counter
from scripts.core.annex_search_chunks import search_documents, MAX_PASSAGE_CHARS, split_text
from scripts.utils.document_blocks import blocks_markdown


def test_long_annex_keeps_display_and_indexes_tail_without_truncation():
    text = '\n'.join(f'항목 {i} 근로 조건 설명' for i in range(2000)) + '\nTAIL_AMOUNT=987654'
    original = dict(chunk_id='law:1:별표2', doc_id='1', content=text,
                    metadata={'is_annex': True, 'document_blocks': [{'kind':'text','text':text,'source_pages':[179]}]})
    parent, *parts = search_documents(original)
    assert parent['content'] == text and parent['metadata']['is_searchable'] is False
    assert all(len(x['content']) <= MAX_PASSAGE_CHARS for x in parts)
    assert ''.join(x['content'] for x in parts) == text
    assert 'TAIL_AMOUNT=987654' in parts[-1]['content']
    assert all(x['metadata']['source_pages'] == [179] for x in parts)
    assert len({x['chunk_id'] for x in parts}) == len(parts)
    assert 'search_parent' not in original['metadata']


def test_large_markdown_table_repeats_header_without_dropping_rows():
    text = '| 직종 | 금액 |\n|---|---|\n' + '\n'.join(f'| 직종{i} | {i} |' for i in range(1000))
    parts = list(split_text(text))
    assert all(x[0].startswith('| 직종 | 금액 |') for x in parts)
    assert ''.join(text[a:b] for _, a,b in parts) == text
    assert all(len(x[0]) <= MAX_PASSAGE_CHARS for x in parts)


def test_short_annex_stays_single_row_and_non_annex_is_unchanged():
    doc=dict(chunk_id='x',content='본문',metadata={'is_annex': True})
    assert len(search_documents(doc)) == 1
    assert search_documents(doc)[0]['metadata']['is_searchable'] is True
    non=dict(chunk_id='y',content='x'*10000,metadata={})
    assert search_documents(non)==[non]
