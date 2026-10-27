"""법령·행정규칙: 조문 일부라도 저장에 실패하면 실패로 돌려준다(성공으로 세지 않는다)."""
import unittest
from unittest.mock import Mock, patch

from scripts.adrule.logic import scraper as adrule_scraper
from scripts.law.logic import scraper as law_scraper

LAW_TEXT = "[제1조(목적)]\n이 법은 목적을 정한다.\n[제2조(정의)]\n이 법에서 쓰는 말의 뜻은 다음과 같다."


def repo_failing_on(call_number: int) -> Mock:
    """n번째 upsert 호출만 예외를 던지는 저장소."""
    repo = Mock()
    calls = {"n": 0}

    def upsert(doc):
        calls["n"] += 1
        if calls["n"] == call_number:
            raise RuntimeError("write failed")
        return doc.get("doc_id"), {"action": "insert", "version": 1, "changed_fields": []}

    repo.upsert_with_change_detection.side_effect = upsert
    return repo


class LawSaveFailureTest(unittest.TestCase):
    def save(self, repo):
        doc = {"doc_id": "law-1", "title": "테스트법", "sub_title": "", "content": LAW_TEXT,
               "url": "https://example.invalid", "metadata": {"source_url": "https://example.invalid"}}
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=Mock()), \
             patch.object(law_scraper, "UnifiedDocumentRepository", return_value=repo), \
             patch.object(law_scraper, "fetch_stable_law_id", return_value="L1"):
            return law_scraper.save_to_mongodb(doc)

    def test_all_articles_saved_is_success(self):
        self.assertEqual(self.save(repo_failing_on(0)), "new_version")

    def test_one_article_failing_is_failure(self):
        repo = repo_failing_on(2)
        self.assertFalse(self.save(repo))
        # 다음 실행이 다시 수집하도록 표시한다(시행일자가 같아도 건너뛰지 않게).
        repo.mark_save_complete.assert_called_once_with("https://example.invalid", complete=False)

    def test_complete_save_clears_mark(self):
        repo = repo_failing_on(0)
        self.save(repo)
        repo.mark_save_complete.assert_called_once_with("https://example.invalid", complete=True)


class IncompleteSaveIsRecrawledTest(unittest.TestCase):
    def test_incomplete_law_is_recrawled_even_with_same_effective_date(self):
        from crawl import filter_new_urls
        items = [{"url": "u1", "effective": "20261101"}, {"url": "u2", "effective": "20261101"}]
        effective_map = {"u1": "2026-11-01", "u2": None}
        self.assertEqual([i["url"] for i in filter_new_urls(items, set(), effective_map=effective_map)], ["u2"])


class AdruleSaveFailureTest(unittest.TestCase):
    def save(self, repo):
        chunks = [{"title": "본문", "text": LAW_TEXT}]
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=Mock()), \
             patch.object(adrule_scraper, "UnifiedDocumentRepository", return_value=repo):
            return adrule_scraper.save_to_mongodb(chunks, "테스트 고시", "adrule-1", "https://example.invalid")

    def test_all_articles_saved_is_success(self):
        self.assertEqual(self.save(repo_failing_on(0)), "new_version")

    def test_one_article_failing_is_failure(self):
        self.assertFalse(self.save(repo_failing_on(2)))


if __name__ == "__main__":
    unittest.main()
