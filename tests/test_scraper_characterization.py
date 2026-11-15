"""수정 전 동작을 고정하는 특성 테스트: 심의결정례 저장 결과, 법령 조문 분리.

네트워크·실제 DB 없이 가짜 컬렉션으로 돌린다. 해석례는 test_interpretation_save_characterization.
"""
import asyncio
import unittest
from unittest.mock import patch

from scripts.decision.logic import scraper as decision_scraper
from scripts.law.logic.scraper import split_articles_by_number
from tests.fakes import FakeDB


class DecisionScrapeAndSaveTest(unittest.TestCase):
    ITEM = {"jgmt_sn": "77", "title": "심의 요지입니다", "registered_at": "2026-10-01", "url": "https://x/d?jgmtSn=77"}

    def run_scrape(self, db):
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
            return asyncio.run(decision_scraper.scrape_and_save(
                self.ITEM, output_dir="/nonexistent", output_name="t", save_jsonl=False))

    def test_save_success(self):
        db = FakeDB()
        result = self.run_scrape(db)
        self.assertEqual(result, {"doc_id": "77", "status": "success"})
        rows = db["decision"].docs
        self.assertEqual([r["doc_id"] for r in rows], ["77"])
        self.assertEqual(rows[0]["metadata"]["current_version"], 1)

    def test_save_failure_is_failed(self):
        db = FakeDB()
        db["decision"].insert_one = lambda doc: (_ for _ in ()).throw(RuntimeError("down"))
        self.assertEqual(self.run_scrape(db)["status"], "failed")


class SplitArticlesByNumberTest(unittest.TestCase):
    def test_splits_numbered_and_sub_numbered_articles(self):
        text = "[제1조(목적)]\n목적 내용\n[제5조의2(정의)]\n정의 내용\n[제6조(적용)]\n적용 내용"
        articles = split_articles_by_number(text)
        self.assertEqual([a["article_number"] for a in articles], ["1", "5.2", "6"])
        self.assertEqual(articles[1]["article_title"], "제5조의2(정의)")
        self.assertEqual(articles[1]["article_number_numeric"], 5.2)
        self.assertEqual(articles[2]["content"], "적용 내용")

    def test_text_without_article_markers_returns_empty(self):
        self.assertEqual(split_articles_by_number("조문 표기가 없는 본문"), [])


if __name__ == "__main__":
    unittest.main()
