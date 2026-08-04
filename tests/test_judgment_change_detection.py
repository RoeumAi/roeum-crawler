import unittest
from unittest.mock import MagicMock, patch

from scripts.judgment.logic import scraper as judgment_scraper
from scripts.core.database.source_versioning import sha256_content


def make_document(doc_id="78", content="판정 본문"):
    return {
        "chunk_id": f"judgment_{doc_id}",
        "doc_id": doc_id,
        "doc_type": "주요판정사례",
        "article_number": "1",
        "title": "판정 제목",
        "sub_title": "2026부해123",
        "content": content,
        "metadata": {
            "source_url": "https://nlrc.go.kr/detail.do",
            "is_active": True,
            "pdf_retry_needed": False,
        },
    }


class JudgmentChangeDetectionTest(unittest.TestCase):
    def _run_save(self, existing, document):
        collection = MagicMock()
        collection.find_one.return_value = existing
        collection.update_one.return_value = MagicMock(upserted_id=None, modified_count=1)
        fake_db = {"judgment": collection}

        with patch(
            "scripts.core.database.mongo_client.get_mongo_db",
            return_value=fake_db,
        ):
            result = judgment_scraper.save_judgment_to_mongodb(document)
        return result, document

    def test_marks_new_version_when_no_existing_doc(self):
        document = make_document()
        result, document = self._run_save(existing=None, document=document)

        self.assertTrue(result)
        self.assertEqual(document["_action"], "new_version")

    def test_marks_no_change_when_content_hash_matches(self):
        document = make_document(content="동일한 판정 본문")
        existing = {"content_hash": sha256_content("동일한 판정 본문")}
        result, document = self._run_save(existing=existing, document=document)

        self.assertTrue(result)
        self.assertEqual(document["_action"], "no_change")

    def test_marks_update_existing_when_content_hash_differs(self):
        document = make_document(content="새로 바뀐 판정 본문")
        existing = {"content_hash": sha256_content("옛날 판정 본문")}
        result, document = self._run_save(existing=existing, document=document)

        self.assertTrue(result)
        self.assertEqual(document["_action"], "update_existing")

    def test_stored_document_does_not_persist_transient_action_field(self):
        document = make_document()
        collection = MagicMock()
        collection.find_one.return_value = None
        collection.update_one.return_value = MagicMock(upserted_id="x", modified_count=0)
        fake_db = {"judgment": collection}

        with patch(
            "scripts.core.database.mongo_client.get_mongo_db",
            return_value=fake_db,
        ):
            judgment_scraper.save_judgment_to_mongodb(document)

        stored = collection.update_one.call_args.args[1]["$set"]
        self.assertNotIn("_action", stored)


class ScrapeAndSaveActionPropagationTest(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_and_save_returns_action_from_save(self):
        html = """
        <table class="BD_table">
          <tr><th class="title">판정 제목</th></tr>
          <tr><th>자료구분</th><td>부당해고</td></tr>
          <tr><th>판정사항</th><td>판정사항 요약</td></tr>
          <tr><th>판정요지</th><td>판정요지 요약</td></tr>
          <tr><th>등록일</th><td>2026-07-28</td></tr>
        </table>
        """
        fallback = {
            "success": False,
            "text": "",
            "content_source": "html_fallback",
            "attachment": None,
            "page_count": None,
            "is_searchable": None,
            "cost_usd": 0.0,
            "error": "not found",
        }

        def fake_save(document):
            document["_action"] = "no_change"
            return True

        with (
            patch.object(judgment_scraper, "_fetch_detail_html", return_value=html),
            patch.object(judgment_scraper, "extract_attachment_text", return_value=fallback),
            patch.object(judgment_scraper, "save_judgment_to_mongodb", side_effect=fake_save),
        ):
            result = await judgment_scraper.scrape_and_save(
                "https://nlrc.go.kr/nlrc/mainCase/judgment/detail.do?jgmtSn=1&jgmtDcsnSeCd=65",
                output_dir="",
                output_name="test",
                save_jsonl=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["action"], "no_change")


if __name__ == "__main__":
    unittest.main()
