"""테스트 공통: 국가법령 Open API 를 부르지 않는다(별표·안정 ID 는 빈 응답으로). 추출 호출은 기다리지 않는다."""
import pytest


@pytest.fixture(autouse=True)
def no_lawgokr_api(monkeypatch):
    from scripts.adrule.logic import scraper as adrule_scraper
    from scripts.law.logic import scraper as law_scraper
    monkeypatch.setattr(law_scraper, "fetch_record", lambda target, doc_id: {})
    monkeypatch.setattr(adrule_scraper, "fetch_record", lambda target, doc_id: {})
    from scripts.utils import document_extraction
    monkeypatch.setattr(document_extraction, "_gate", document_extraction._ExtractGate(sleep=lambda seconds: None, healthy=lambda url: True))


@pytest.fixture(autouse=True)
def fresh_index_cache():
    """저장소 인덱스는 프로세스당 한 번만 만든다 — 테스트끼리 기록을 공유하지 않게 비운다."""
    from scripts.core.database import unified_repository
    unified_repository.reset_index_cache()
