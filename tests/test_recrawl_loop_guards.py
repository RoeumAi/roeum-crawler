"""
"2027년 적용 최저임금 고시" 재크롤링 무한루프 회귀 테스트.

원인 체인:
1. adrule 저장 경로가 metadata.created_at을 설정하지 않아 None으로 저장됨
2. refresh 재계산이 같은 effective의 upcoming 중복에서 created_at="" 문서를 비활성화
3. get_crawled_effective_dates가 is_active=True만 조회해 비활성 문서 URL이
   effective_map에서 빠짐 → 매일 신규로 오인 → 재크롤링 → 중복 청크 무한 적재
"""

import unittest
from unittest.mock import Mock, patch

from scripts.core.database.change_detector import VersionManager
from scripts.core.database.refresh_promotion import (
    group_active_documents_by_stable_id,
    plan_group_transitions,
)


class FakeCursor(list):
    def max_time_ms(self, _ms):
        return self


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents

    def _get(self, doc, dotted_key):
        value = doc
        for part in dotted_key.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def find(self, query=None, projection=None):
        query = query or {}
        results = []
        for doc in self.documents:
            matched = True
            for key, condition in query.items():
                value = self._get(doc, key)
                if isinstance(condition, dict) and "$in" in condition:
                    if value not in condition["$in"]:
                        matched = False
                        break
                elif isinstance(condition, dict) and "$exists" in condition:
                    if (value is not None) != condition["$exists"]:
                        matched = False
                        break
                elif value != condition:
                    matched = False
                    break
            if matched:
                results.append(doc)
        return FakeCursor(results)


class FakeDb:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, _name):
        return self.collection


class GetCrawledEffectiveDatesTest(unittest.TestCase):
    """crawl.get_crawled_effective_dates가 비활성 문서도 존재로 인정해야 한다."""

    def _run(self, documents, url_list=None):
        from crawl import get_crawled_effective_dates

        fake_db = FakeDb(FakeCollection(documents))
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=fake_db):
            return get_crawled_effective_dates("adrule", url_list=url_list)

    def test_active_document_included(self):
        documents = [
            {"metadata": {"source_url": "urlA", "effective": "2026-01-01", "is_active": True}},
        ]
        self.assertEqual(self._run(documents), {"urlA": "2026-01-01"})

    def test_inactive_only_document_still_included(self):
        # 재계산이 비활성화한 문서(최저임금 고시 케이스)도 effective_map에 남아
        # 매일 신규로 오인되지 않아야 한다.
        documents = [
            {"metadata": {"source_url": "urlB", "effective": "2027-01-01", "is_active": False}},
        ]
        self.assertEqual(self._run(documents), {"urlB": "2027-01-01"})

    def test_active_version_wins_over_inactive(self):
        documents = [
            {"metadata": {"source_url": "urlC", "effective": "2025-01-01", "is_active": False}},
            {"metadata": {"source_url": "urlC", "effective": "2026-01-01", "is_active": True}},
            {"metadata": {"source_url": "urlC", "effective": "2024-01-01", "is_active": False}},
        ]
        self.assertEqual(self._run(documents), {"urlC": "2026-01-01"})

    def test_inactive_versions_prefer_latest_effective(self):
        documents = [
            {"metadata": {"source_url": "urlD", "effective": "2025-01-01", "is_active": False}},
            {"metadata": {"source_url": "urlD", "effective": "2027-01-01", "is_active": False}},
        ]
        self.assertEqual(self._run(documents), {"urlD": "2027-01-01"})

    def test_url_list_filter_matches_inactive_documents(self):
        documents = [
            {"metadata": {"source_url": "urlE", "effective": "2027-01-01", "is_active": False}},
            {"metadata": {"source_url": "urlF", "effective": "2026-01-01", "is_active": True}},
        ]
        self.assertEqual(self._run(documents, url_list=["urlE"]), {"urlE": "2027-01-01"})


class VersionManagerCreatedAtTest(unittest.TestCase):
    """저장 경로에서 metadata.created_at이 항상 채워져야 한다."""

    def test_prepare_version_update_fills_missing_created_at(self):
        new_doc = {"metadata": {"source_url": "u", "effective": "2027-01-01"}}

        metadata = VersionManager.prepare_version_update(None, new_doc, {"changed_fields": []})

        self.assertTrue(metadata.get("created_at"))

    def test_prepare_version_update_keeps_scraper_created_at(self):
        new_doc = {"metadata": {"created_at": "2026-08-01"}}

        metadata = VersionManager.prepare_version_update(None, new_doc, {"changed_fields": []})

        self.assertEqual(metadata["created_at"], "2026-08-01")

    def test_prepare_metadata_update_backfills_none_created_at(self):
        # 기존 문서 created_at이 None(과거 버그 데이터)이면 새 문서 값으로 백필한다.
        current_doc = {"metadata": {"version": 1, "created_at": None, "updated_at": None}}
        new_doc = {"metadata": {"created_at": "2026-08-13"}}

        metadata = VersionManager.prepare_metadata_update(current_doc, new_doc)

        self.assertEqual(metadata["created_at"], "2026-08-13")

    def test_prepare_metadata_update_keeps_existing_created_at(self):
        current_doc = {"metadata": {"version": 1, "created_at": "2026-07-01", "updated_at": "2026-07-01"}}
        new_doc = {"metadata": {"created_at": "2026-08-13"}}

        metadata = VersionManager.prepare_metadata_update(current_doc, new_doc)

        self.assertEqual(metadata["created_at"], "2026-07-01")


class AdruleSaveCreatedAtTest(unittest.TestCase):
    """adrule 저장 경로가 metadata.created_at을 설정해야 한다."""

    def test_save_to_mongodb_sets_created_at(self):
        from scripts.adrule.logic import scraper as adrule_scraper

        fake_repo = Mock()
        fake_repo.upsert_with_change_detection.return_value = ("2100000283564", {"action": "insert"})

        with (
            patch("scripts.core.database.mongo_client.get_mongo_db", return_value=Mock()),
            patch.object(adrule_scraper, "UnifiedDocumentRepository", return_value=fake_repo),
        ):
            result = adrule_scraper.save_to_mongodb(
                chunks=[{"title": "고시", "text": "2027년 적용 최저임금은 다음과 같다."}],
                doc_title="2027년 적용 최저임금 고시",
                doc_id="2100000283564",
                url="https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=2100000283564",
                effective_date="2027-01-01",
                is_upcoming=True,
                adrule_id="2002374",
            )

        self.assertEqual(result, "new_version")
        saved_docs = [call.args[0] for call in fake_repo.upsert_with_change_detection.call_args_list]
        self.assertTrue(saved_docs)
        for doc in saved_docs:
            self.assertTrue(doc["metadata"].get("created_at"),
                            "adrule 저장 문서에 created_at이 없습니다")


class UpcomingTieBreakFallbackTest(unittest.TestCase):
    """created_at이 없는 문서는 updated_at으로 tie-break해야 한다."""

    def test_grouping_falls_back_to_updated_at(self):
        documents = [
            {"doc_id": "d1", "metadata": {"adrule_id": "G1", "effective": "2027-01-01",
                                          "is_upcoming": True, "created_at": None,
                                          "updated_at": "2026-08-07T10:00:00"}},
        ]

        groups = group_active_documents_by_stable_id(documents, "adrule_id")

        self.assertEqual(groups["G1"]["d1"]["created_at"], "2026-08-07T10:00:00")

    def test_minwage_scenario_confirmed_notice_wins_over_draft(self):
        # 확정 고시(created_at=None, 나중에 크롤링돼 updated_at이 최신)가
        # 구 초안(created_at=2026-07-21)을 이겨야 한다.
        documents = [
            {"doc_id": "2100000282670",  # 초안: 2027년 적용 최저임금안 고시
             "metadata": {"adrule_id": "2002374", "effective": "2027-01-01",
                          "is_upcoming": True, "created_at": "2026-07-21",
                          "updated_at": "2026-07-21T09:00:00"}},
            {"doc_id": "2100000283564",  # 확정: 2027년 적용 최저임금 고시
             "metadata": {"adrule_id": "2002374", "effective": "2027-01-01",
                          "is_upcoming": True, "created_at": None,
                          "updated_at": "2026-08-07T09:00:00"}},
        ]

        groups = group_active_documents_by_stable_id(documents, "adrule_id")
        plan = plan_group_transitions(groups["2002374"], today="2026-08-13")

        self.assertEqual(plan["keep_upcoming"], ["2100000283564"])
        self.assertEqual(plan["deactivate"], ["2100000282670"])


if __name__ == "__main__":
    unittest.main()
