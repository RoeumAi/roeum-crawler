"""테스트 공통: 국가법령 Open API 를 부르지 않는다(별표·안정 ID 는 빈 응답으로). 별표 파서 호출은 기다리지 않는다."""
import pytest


@pytest.fixture(autouse=True)
def no_lawgokr_api(monkeypatch):
    from scripts.adrule.logic import scraper as adrule_scraper
    from scripts.law.logic import scraper as law_scraper
    monkeypatch.setattr(law_scraper, "fetch_record", lambda target, doc_id: {})
    monkeypatch.setattr(adrule_scraper, "fetch_record", lambda target, doc_id: {})
    from scripts.core import lawgokr_annex
    monkeypatch.setattr(lawgokr_annex, "_gate", lawgokr_annex._ExtractGate(sleep=lambda seconds: None))
