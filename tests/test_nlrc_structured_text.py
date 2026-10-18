from unittest.mock import patch

from scripts.utils.nlrc_pdf import extract_attachment_text


def test_db_prefers_paragraphs_and_keeps_empty_table_columns():
    html = '<a data-nlrc-event="click-download" data-file-id="1" data-key="1" title="사례.pdf"></a>'
    payload = {'is_success': True, 'full_text': 'flattened',
               'structured_text': '앞\r\n\r\n항목\t\t금액\r뒤', 'file_type': 'pdf',
               'extraction_status': 'complete', 'document_blocks': [{'kind':'text','text':'앞'}],
               'extraction_metadata': {'paragraph_projection': {'status':'complete'}}}
    with patch('scripts.utils.nlrc_pdf._download_pdf', return_value=b'%PDF'), \
         patch('scripts.utils.nlrc_pdf._call_extract_text', return_value=payload):
        result = extract_attachment_text(html, 'https://nlrc.go.kr/')
    assert result['success']
    assert result['text'] == '[첨부: 사례.pdf]\n\n앞\n\n항목\t\t금액\n뒤'


def test_partial_or_unverified_pdf_returns_text_with_quality_warning():
    html = '<a data-nlrc-event="click-download" data-file-id="1" data-key="1" title="사례.pdf"></a>'
    for status, projection in [('partial', 'complete'), ('complete', 'needs_review'), ('complete', None)]:
        payload = {'is_success': True, 'full_text': '일부 읽은 본문', 'file_type': 'pdf',
                   'extraction_status': status, 'document_blocks': [{'kind':'text','text':'본문'}],
                   'extraction_metadata': {'paragraph_projection': {'status': projection}}}
        with patch('scripts.utils.nlrc_pdf._download_pdf', return_value=b'%PDF'), \
             patch('scripts.utils.nlrc_pdf._call_extract_text', return_value=payload):
            result = extract_attachment_text(html, 'https://nlrc.go.kr/')
        assert result['success'] and result['retry_needed']
        assert result['extraction_status'] == 'partial'
        assert '일부 읽은 본문' in result['text']
        assert result['document_blocks'][-1] == {'kind':'text','text':'일부 읽은 본문'}


def test_one_failed_attachment_never_publishes_the_other_as_complete():
    html = ''.join(f'<a data-nlrc-event="click-download" data-file-id="{i}" data-key="1" title="사례{i}.pdf"></a>' for i in (1,2))
    complete = {'is_success': True, 'structured_text':'본문', 'extraction_status':'complete',
                'document_blocks':[{'kind':'text','text':'본문'}],
                'extraction_metadata':{'paragraph_projection':{'status':'complete'}}}
    with patch('scripts.utils.nlrc_pdf._download_pdf', return_value=b'%PDF'), \
         patch('scripts.utils.nlrc_pdf._call_extract_text', side_effect=[complete, RuntimeError('read failed')]):
        result = extract_attachment_text(html, 'https://nlrc.go.kr/')
    assert result['success'] and result['retry_needed']
    assert result['extraction_status'] == 'partial'
    assert result['text'] == '[첨부: 사례1.pdf]\n\n본문'
    assert result['document_blocks'][-1]['text'] == '본문'
    from scripts.utils.nlrc_pdf import attachment_display_metadata
    displayed = attachment_display_metadata('요약', result)
    assert displayed['paragraph_projection']['status'] == 'needs_review'
    assert displayed['document_blocks'][1]['text'] == '[첨부파일 추출 본문 · 일부 미확인]'


def test_failed_refresh_updates_only_retry_metadata():
    from unittest.mock import Mock
    from scripts.utils.nlrc_pdf import preserve_existing_after_failure
    collection = Mock()
    existing = {'_id': 'stable-id', 'content_hash':'original-hash',
                'content': '보존해야 하는 기존 본문이다. ' * 20, 'metadata': {'pdf_retry_count': 5}}
    document = {'doc_id':'1','content':'읽힌 부분 본문','metadata':{
        'pdf_retry_needed':True,'pdf_error':'partial','pdf_extraction_status':'partial'}}
    assert preserve_existing_after_failure(collection, existing, document)
    query, update = collection.update_one.call_args.args
    assert query == {'_id':'stable-id'}
    assert all(key.startswith('metadata.pdf_') for key in update['$set'])
    assert update['$set']['metadata.pdf_retry_count']==6
    assert update['$set']['metadata.pdf_last_attempt_status']=='partial'
    assert document['_action']=='no_change'


def test_plain_hwp_text_cannot_claim_verified_paragraph_or_table_structure():
    from scripts.utils.nlrc_pdf import attachment_display_metadata
    result = {'success': True, 'document_blocks': [{'kind':'text','text':'표가 평문으로 추출됨'}],
              'extractions': [{'extraction_status':'complete','metadata':{'layout_status':'unverified'}}]}
    assert attachment_display_metadata('요약', result)['paragraph_projection']['status']=='needs_review'
    result['extractions'][0]['metadata']['paragraph_projection']={'status':'complete'}
    assert attachment_display_metadata('요약', result)['paragraph_projection']['status']=='complete'


def test_source_html_character_references_do_not_leak_into_title_or_summary():
    from scripts.judgment.logic.scraper import _parse_detail
    from scripts.mediation_case.logic.scraper import clean_text
    source = '''<table class="BD_table"><tr><th class="title">지배&amp;#8228;개입&amp;nbsp;사례</th></tr>
    <tr><th>판정사항</th><td>첫 문단&amp;nbsp;본문\n\n둘째 문단 &amp;lt;관련자료&amp;gt;</td></tr></table>'''
    parsed = _parse_detail(source)
    assert parsed['title'] == '지배․개입 사례'
    assert parsed['judgment_points'] == '첫 문단 본문\n\n둘째 문단 <관련자료>'
    assert clean_text('조정&#8228;중재\n\n본문') == '조정․중재\n\n본문'
