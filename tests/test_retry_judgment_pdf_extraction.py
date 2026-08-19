"""judgment PDF 재추출 마이그레이션 — 제자리 갱신 계약을 고정한다.

레거시 문서의 doc_id("1","100")와 URL의 jgmtSn("13896")이 다르므로, 재추출이
식별자를 건드리면 같은 문서가 두 doc_id 로 중복 생성된다. 여기서는
(1) 식별자(doc_id/chunk_id/article_number)가 $set 에 포함되지 않고
(2) metadata 는 dotted 경로라 임베딩 파이프라인 키를 보존하며
(3) 사건번호가 본문 폴백으로 채워지는 것을 못박는다.
"""

from scripts.migrations import retry_judgment_pdf_extraction as m


class FakeCollection:
    def __init__(self):
        self.calls = []

    def update_many(self, query, update):
        self.calls.append((query, update))
        class R:
            modified_count = 1
        return R()


def test_reextract_updates_in_place_without_touching_identity(monkeypatch):
    monkeypatch.setattr(m, "_fetch_detail_html", lambda url: "<html></html>")
    monkeypatch.setattr(m, "_parse_detail", lambda html: {
        "title": "부당해고 구제 재심신청 사례",
        "judgment_points": "판정사항 텍스트",
        "judgment_summary": "판정요지 텍스트",
    })
    monkeypatch.setattr(m, "extract_attachment_text", lambda html, url: {
        "success": True,
        "text": "중앙노동위원회 판정서\n사    건 중앙2016부해1234 부당해고 구제 재심신청",
        "content_source": "pdf_ocr",
        "page_count": 5,
        "is_searchable": False,
        "cost_usd": 0.03,
        "attachment": {"name": "사례1.pdf", "download_url": "http://x", "file_id": "f1"},
    })

    collection = FakeCollection()
    row = {
        "doc_id": "1",  # 레거시 정수형 — URL 의 jgmtSn 과 다르다
        "chunk_id": "legacy:1",
        "article_number": "1",
        "title": "옛 제목",
        "sub_title": "",
        "metadata": {"source_url": "https://nlrc.go.kr/detail.do?jgmtSn=13896"},
    }

    outcome = m.reextract_one(collection, row)

    assert outcome == "updated"
    (query, update), = collection.calls
    assert query == {"doc_id": "1"}  # 기존 doc_id 로만 갱신
    set_fields = update["$set"]
    # 식별자는 $set 에 없어야 한다 — 있으면 jgmtSn 으로 갈아치워져 중복이 생긴다
    for forbidden in ("doc_id", "chunk_id", "article_number"):
        assert forbidden not in set_fields
    # metadata 는 dotted 경로 (통째 교체 금지 — is_searchable/embedding_* 보존)
    assert "metadata" not in set_fields
    assert set_fields["metadata.pdf_error"] == ""
    assert set_fields["metadata.pdf_retry_needed"] is False
    # 사건번호는 본문 '사 건' 라벨에서 복구된다
    assert set_fields["sub_title"] == "중앙2016부해1234"
    assert "[첨부파일 전문]" in set_fields["content"]


def test_ocr_failure_leaves_document_untouched(monkeypatch):
    monkeypatch.setattr(m, "_fetch_detail_html", lambda url: "<html></html>")
    monkeypatch.setattr(m, "_parse_detail", lambda html: {"title": "t"})
    monkeypatch.setattr(m, "extract_attachment_text", lambda html, url: {
        "success": False, "error": "OCR down",
    })

    collection = FakeCollection()
    row = {"doc_id": "2", "metadata": {"source_url": "https://nlrc.go.kr/x"}}

    assert m.reextract_one(collection, row) == "ocr_failed"
    assert collection.calls == []  # 실패 시 어떤 필드도 덮지 않는다


def test_missing_source_url_is_skipped():
    collection = FakeCollection()
    assert m.reextract_one(collection, {"doc_id": "3", "metadata": {}}) == "no_url"
    assert collection.calls == []
