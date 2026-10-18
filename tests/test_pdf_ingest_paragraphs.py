from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from scripts.pdf_ingest.pdf_to_mongo import (make_doc_id, split_text, process_directory,
    parse_case_filename, parse_interpretation_filename, parse_decision_filename)


def test_existing_decomposed_unicode_id_is_unchanged():
    name = '광주고등법원 2023. 11. 1. 선고 (제주)2022나10981 판결'
    assert make_doc_id('case', name) == 'pdf_case__________________2023__11__1___484c9372'


@pytest.mark.parametrize('parser,name,field,expected', [
    (parse_case_filename, '광주고등법원 2023. 11. 1. 선고 (제주)2022나10981 판결', 'case_number', '(제주)2022나10981'),
    (parse_interpretation_filename, '고용노동부 근로기준과-2328 (2004.5.12)', 'doc_number', '근로기준과-2328'),
    (parse_decision_filename, '서울지방노동위원회 2017부해665 (2017. 5. 17)', 'case_number', '2017부해665'),
])
def test_mac_decomposed_filenames_keep_readable_metadata(parser, name, field, expected):
    import unicodedata
    parsed = parser(unicodedata.normalize('NFD', name))
    assert parsed == parser(name)
    assert parsed[field] == expected


def test_search_chunks_never_split_or_duplicate_words():
    words = ['하나의', '단일한', '판단으로', '살펴볼', '필요가', '있다.'] * 10
    chunks = split_text(' '.join(words), max_chars=15)
    assert [word for chunk in chunks for word in chunk.split()] == words
    assert all(len(chunk)<=15 for chunk in chunks)


def test_local_ingest_requires_complete_common_parser_and_saves_canonical_display(tmp_path):
    source = tmp_path / '대법원 2023. 11. 1. 선고 2023다123 판결.pdf'
    source.write_bytes(b'%PDF source')
    text = '하나의 단일한 판단으로 살펴볼 필요가 있다. ' * 180
    blocks = [{'kind':'text','text':text}]
    complete = dict(is_success=True,extraction_status='complete',structured_text=text,
                    document_blocks=blocks,extraction_metadata={'paragraph_projection':{'status':'complete','version':'legal-paragraphs-v1'}})
    collection=Mock()
    collection.find_one.return_value=None
    with patch('scripts.pdf_ingest.pdf_to_mongo.call_extract_text',return_value=complete) as extract:
        counts=process_directory('판례',tmp_path,'case',{'case':collection},False)
    assert counts==(1,0,0)
    assert extract.call_args.args==(b'%PDF source',source.name)
    saved=[call.args[1]['$set'] for call in collection.update_one.call_args_list]
    assert len(saved)>1
    assert saved[0]['metadata']['document_blocks']==blocks
    assert saved[0]['metadata']['document_text']==text
    assert saved[0]['metadata']['document_blocks_scope']=='section'
    assert all('document_blocks' not in doc['metadata'] for doc in saved[1:])
    assert all(doc['content_hash'] and doc['source_version_id'] for doc in saved)
    collection.reset_mock()
    with patch('scripts.pdf_ingest.pdf_to_mongo.call_extract_text',return_value=dict(complete,extraction_status='partial')):
        assert process_directory('판례',tmp_path,'case',{'case':collection},False)==(0,0,1)
    collection.update_one.assert_not_called()
