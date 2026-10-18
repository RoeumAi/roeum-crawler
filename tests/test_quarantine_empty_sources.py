from scripts.migrations.quarantine_empty_sources import plan_quarantine, apply_quarantine


class Collection:
    def __init__(self, rows): self.rows = rows
    def find(self, *args): return self.rows
    def update_one(self, query, update):
        assert query['content'] == '제목: 퇴직금\n출처: https://example.com'
        assert query['metadata'] == {'is_searchable': True}
        assert update['$set'] == {'metadata.is_stub': True, 'metadata.is_searchable': False}
        return type('Result', (), {'modified_count': 1})()


def test_repaired_or_partial_documents_are_not_quarantined():
    db = {'case': Collection([{'title': '판례', 'content': '실제 본문', 'metadata': {'is_metadata_only': True}}])}
    assert not plan_quarantine(db, [{'collection': 'case', 'doc_id': '1'}])
    db['case'].rows.append({'title': '판례', 'content': '판례'})
    assert not plan_quarantine(db, [{'collection': 'case', 'doc_id': '1'}])


def test_backup_precedes_conditional_update(tmp_path):
    row = {'_id': '1', 'doc_id': '1', 'title': '퇴직금', 'content': '제목: 퇴직금\n출처: https://example.com', 'metadata': {'is_searchable': True}}
    db = {'case': Collection([row])}
    plans = plan_quarantine(db, [{'collection': 'case', 'doc_id': '1'}])
    backup = tmp_path / 'before.json'
    assert apply_quarantine(db, plans, backup) == 1
    assert backup.exists()
    import pytest
    with pytest.raises(FileExistsError):
        apply_quarantine(db, plans, backup)
