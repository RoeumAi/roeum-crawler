from unittest.mock import Mock
from scripts.utils.nlrc_pdf import preserve_existing_after_failure


def test_available_partial_refresh_is_not_blocked_by_quality_flag():
    collection = Mock()
    old = {'_id': 'id', 'content': '기존 본문이다.'}
    new = {'doc_id': 'document', 'content': '새로 복원한 본문이다.',
           'metadata': {'pdf_retry_needed': True, 'pdf_extraction_status': 'partial'}}
    assert not preserve_existing_after_failure(collection, old, new)
    collection.update_one.assert_not_called()


def test_major_loss_partial_preserves_old_body_and_records_retry():
    collection = Mock()
    old = {'_id': 'id', 'content': '충분한 기존의 본문이다. ' * 50}
    new = {'doc_id': 'document', 'content': '한 줄',
           'metadata': {'pdf_retry_needed': True, 'pdf_extraction_status': 'partial'}}
    assert preserve_existing_after_failure(collection, old, new)
    fields = collection.update_one.call_args.args[1]['$set']
    assert 'content' not in fields and 'embedding' not in fields
    assert fields['metadata.pdf_last_attempt_status'] == 'partial'
