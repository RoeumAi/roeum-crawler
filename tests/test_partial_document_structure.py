from scripts.utils.document_extraction import usable_document


def test_partial_keeps_parser_assembled_structure_and_quality_warning():
    blocks = [{'kind': 'text', 'role': 'heading', 'text': '제목'},
              {'kind': 'text', 'role': 'paragraph', 'text': '이어지는 본문'}]
    result = usable_document(dict(is_success=True, extraction_status='partial',
        full_text='제목\n이어지는\n본문', structured_text='제목\n\n이어지는 본문',
        document_blocks=blocks, extraction_metadata={'failed_page_numbers': [2]}))
    assert result['document_blocks'] == blocks
    assert result['structured_text'] == '제목\n\n이어지는 본문'
    assert not result['quality_complete']
    assert result['extraction_metadata']['paragraph_projection']['status'] == 'needs_review'
    assert result['extraction_metadata']['failed_page_numbers'] == [2]


def test_partial_without_structure_still_returns_available_text():
    result = usable_document(dict(is_success=True, extraction_status='partial', full_text='읽힌 원문'))
    assert result['document_blocks'] == [{'kind': 'text', 'text': '읽힌 원문'}]
    assert result['structured_text'] == '읽힌 원문'
    assert not result['quality_complete']
