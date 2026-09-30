import hashlib
import unicodedata
from scripts.pdf_ingest.pdf_to_mongo import parse_case_filename, parse_interpretation_filename, make_doc_id, process_directory


def test_nfd_filenames():
    for parse, name, field, expected in [
        (parse_case_filename, '대법원 2013. 6. 27. 선고 2011다44276 판결', 'case_number', '2011다44276'),
        (parse_case_filename, '광주고등법원-2016나10826', 'case_number', '2016나10826'),
        (parse_interpretation_filename, '고용노동부 근로기준과-2328 (2004.5.12)', 'doc_number', '근로기준과-2328'),
        (parse_interpretation_filename, '감독 32130-844 (1991.3.26)', 'doc_number', '감독 32130-844'),
    ]:
        parsed = parse(unicodedata.normalize('NFD', name))
        assert parsed[field] == expected
        assert unicodedata.is_normalized('NFC', parsed['title'])


def test_id_hash_keeps_original_filename_bytes():
    name = unicodedata.normalize('NFD', '대법원-2011다44276')
    assert make_doc_id('case', name).endswith(hashlib.md5(name.encode()).hexdigest()[:8])


def test_case_ingestion_keeps_full_case_identifier(tmp_path, monkeypatch):
    name = '대법원 2013. 6. 27. 선고 2011다44276 판결'
    (tmp_path / (name + '.pdf')).touch()
    monkeypatch.setattr('scripts.pdf_ingest.pdf_to_mongo.extract_pdf_text', lambda _: '실질 판결 본문입니다.')
    class Collection:
        rows = []
        def find_one(self, query): return None
        def update_one(self, query, update, **kwargs): self.rows.append(update['$set'])
    col = Collection()
    process_directory('판례', tmp_path, 'case', {'case': col}, False)
    assert '2011다44276' in col.rows[0]['sub_title']
    assert col.rows[0]['metadata']['source_file'] == name + '.pdf'
