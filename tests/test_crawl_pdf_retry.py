import unittest
from unittest.mock import patch

from crawl import candidate_doc_ids, filter_new_urls, get_all_crawled_urls, get_retry_doc_id_filter


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def distinct(self, field, query=None):
        query = query or {}

        def matches(doc):
            meta = doc.get("metadata", {})
            for key, value in query.items():
                if key == "doc_id":
                    if doc["doc_id"] not in value["$in"]:
                        return False
                elif meta.get(key.removeprefix("metadata.")) != value:
                    return False
            return True

        return list({doc[field] for doc in self.docs if matches(doc)})


class FakeDb(dict):
    pass


class RetryDocIdFilterTest(unittest.TestCase):
    def test_nlrc_pdf_collections_use_active_retry_records(self):
        expected = {"metadata.is_active": True, "metadata.pdf_retry_needed": True}

        self.assertEqual(get_retry_doc_id_filter("judgment"), expected)
        self.assertEqual(get_retry_doc_id_filter("mediation_case"), expected)

    def test_other_collections_have_no_retry_filter(self):
        self.assertIsNone(get_retry_doc_id_filter("case"))


class CrawledUrlsTest(unittest.TestCase):
    def _crawled(self, collection_name, docs, url_items=None):
        db = FakeDb({collection_name: FakeCollection(docs)})
        with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
            return get_all_crawled_urls(collection_name, url_items=url_items)

    def test_inactive_old_version_does_not_hide_active_retry(self):
        # 조정사건례 34·41: 이전 버전(inactive)에는 재시도 표시가 없다.
        docs = [
            {"doc_id": "34", "metadata": {"is_active": False}},
            {"doc_id": "34", "metadata": {"is_active": True, "pdf_retry_needed": True}},
            {"doc_id": "35", "metadata": {"is_active": True, "pdf_retry_needed": False}},
        ]

        self.assertEqual(self._crawled("mediation_case", docs), {"35"})

    def test_inactive_retry_flag_alone_does_not_force_recrawl(self):
        docs = [
            {"doc_id": "50", "metadata": {"is_active": False, "pdf_retry_needed": True}},
            {"doc_id": "50", "metadata": {"is_active": True}},
        ]

        self.assertEqual(self._crawled("judgment", docs), {"50"})

    def test_other_collections_skip_every_existing_doc_id(self):
        docs = [{"doc_id": "1", "metadata": {"is_active": True, "pdf_retry_needed": True}}]

        self.assertEqual(self._crawled("case", docs), {"1"})

    def test_scoped_lookup_matches_only_todays_candidates(self):
        # 전체 distinct 대신 그날 목록 후보만 조회한다. 숫자로 저장된 doc_id 도 찾는다.
        docs = [{"doc_id": "100", "metadata": {}}, {"doc_id": 200, "metadata": {}},
                {"doc_id": "999", "metadata": {}}]
        items = [{"url": "https://x/detail.do?precSeq=100"}, {"url": "https://x/detail.do?jgmtSn=200"},
                 {"url": "https://x/detail.do?precSeq=300"}]

        crawled = self._crawled("case", docs, url_items=items)

        self.assertEqual(crawled, {"100", "200"})
        self.assertEqual([i["url"] for i in filter_new_urls(items, crawled)], ["https://x/detail.do?precSeq=300"])

    def test_scoped_lookup_keeps_retry_exclusion(self):
        docs = [{"doc_id": "34", "metadata": {"is_active": True, "pdf_retry_needed": True}},
                {"doc_id": "35", "metadata": {"is_active": True}}]
        items = [{"url": "https://x?jgmtSn=34"}, {"url": "https://x?jgmtSn=35"}]

        self.assertEqual(self._crawled("mediation_case", docs, url_items=items), {"35"})

    def test_candidates_include_raw_value_and_number(self):
        self.assertEqual(candidate_doc_ids([{"doc_seq": "7"}, {"url": "a?expcSeq=8"}, "b"]),
                         {"7", "a?expcSeq=8", "8", "b"})

    def test_mongo_failure_raises_instead_of_empty_set(self):
        # 빈 집합을 돌려주면 전체를 신규로 보고 대량 재수집한다.
        with patch("scripts.core.database.mongo_client.get_mongo_db", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                get_all_crawled_urls("case")


if __name__ == "__main__":
    unittest.main()
