from copy import deepcopy
from scripts.pdf_ingest.reparse_existing import repartition, replacement_document


def test_repartition_preserves_every_word_and_existing_chunk_count():
    text = '첫 번째 문단이다.\n\n다음 문단의 모든 단어를 그대로 유지한다.'
    for lengths in ([1], [10, 10], [5, 10, 20], [1] * 9):
        chunks = repartition(text, lengths)
        assert len(chunks) == len(lengths)
        assert ' '.join(' '.join(chunks).split()) == ' '.join(text.split())


def test_partial_replaces_usable_text_but_never_promotes_quality():
    before = [dict(_id='old-id', doc_id='same-document', chunk_id='same-chunk', title='제목',
                   content='기존의 본문이다.', metadata={'is_searchable': True, 'embed_recipe': 'title+whole@4096'})]
    original = deepcopy(before)
    result = replacement_document(before, dict(is_success=True, extraction_status='partial',
        full_text='새로 읽힌\n본문이다.', structured_text='새로 읽힌 본문이다.',
        document_blocks=[dict(kind='text', text='새로 읽힌 본문이다.')]), 'case', {'version': 'test'})
    assert result['action'] == 'replace'
    assert not result['quality_complete']
    row = result['after'][0]
    assert row['_id'] == 'old-id' and row['chunk_id'] == 'same-chunk'
    assert row['metadata']['pdf_extraction_status'] == 'partial'
    assert row['metadata']['document_text'] == '새로 읽힌 본문이다.'
    assert row['content_hash'] and row['source_version_id']
    assert result['embeddings'][0]['embedding_input'] == '제목\n새로 읽힌 본문이다.'
    assert before == original


def test_empty_or_major_loss_keeps_existing_document():
    before = [dict(content='기존 본문을 잃어서는 안 된다. ' * 100)]
    assert replacement_document(before, {}, 'case', {})['reason'] == 'no_usable_extraction'
    result = replacement_document(before, dict(is_success=True, extraction_status='partial', full_text='한 줄'), 'case', {})
    assert result['action'] == 'retain' and result['reason'] == 'major_text_loss'
