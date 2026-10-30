"""섹션 저장: 같은 제목 섹션이 서로 덮어쓰지 않고, 일부만 저장된 문서는 다시 수집 대상이 된다."""
import unittest
from unittest.mock import patch

from crawl import get_all_crawled_urls
from scripts.case.logic import scraper as case_scraper
from scripts.core.database import section_chunks
from tests.fakes import FakeDB

URL = "https://www.law.go.kr/LSW/detcInfoP.do?detcSeq=900"


def chunk(title, text):
    return {"title": title, "text": text, "metadata": {}}


def save_section(db, chunks, collection="constitutional_decc"):
    with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db), \
         patch.object(section_chunks, "split_section", lambda text: [(text, 1)]):
        return section_chunks.save_section_chunks(collection, "900", "제목", "부제", URL, chunks)


def save_case(db, chunks):
    with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db), \
         patch.object(section_chunks, "split_section", lambda text: [(text, 1)]):
        return case_scraper.save_case_chunks_to_mongodb("900", "제목", "부제", URL, chunks)


SAVERS = {"section_chunks": (save_section, "constitutional_decc"), "case": (save_case, "case")}


def rows_of(db, name):
    return sorted((d["doc_type"], d["chunk_seq"], d["content"]) for d in db[name].docs)


class SectionSaveRegressionTest(unittest.TestCase):
    def each_saver(self):
        for label, (saver, name) in SAVERS.items():
            with self.subTest(saver=label):
                yield saver, name, FakeDB()

    def test_same_title_sections_keep_every_row(self):
        for saver, name, db in self.each_saver():
            chunks = [chunk("주문", "가"), chunk("이유", "하나"), chunk("이유", "둘")]
            self.assertTrue(saver(db, chunks))
            self.assertEqual(rows_of(db, name), [("이유", 1, "하나"), ("이유", 2, "둘"), ("주문", 1, "가")])
            chunk_ids = [d["chunk_id"] for d in db[name].docs]
            self.assertEqual(len(set(chunk_ids)), 3)

    def test_documents_without_duplicate_titles_keep_existing_keys(self):
        for saver, name, db in self.each_saver():
            self.assertTrue(saver(db, [chunk("주문", "가"), chunk("이유", "나")]))
            self.assertEqual([(t, s) for t, s, _ in rows_of(db, name)], [("이유", 1), ("주문", 1)])

    def test_resaving_is_idempotent_and_stale_duplicate_is_deactivated(self):
        for saver, name, db in self.each_saver():
            saver(db, [chunk("이유", "하나"), chunk("이유", "둘")])
            saver(db, [chunk("이유", "하나"), chunk("이유", "둘")])
            self.assertEqual(len(db[name].docs), 2)
            saver(db, [chunk("이유", "하나")])  # 둘째 섹션이 사라진 경우
            by_seq = {d["chunk_seq"]: d["metadata"]["is_active"] for d in db[name].docs}
            self.assertEqual(by_seq, {1: True, 2: False})

    def test_partial_save_is_failed_marked_and_recollected(self):
        for saver, name, db in self.each_saver():
            db[name].fail_update = lambda update, filter_: filter_.get("doc_type") == "이유"
            self.assertFalse(saver(db, [chunk("주문", "가"), chunk("이유", "나")]))
            flags = [d["metadata"].get("save_incomplete") for d in db[name].docs]
            self.assertEqual(flags, [True])
            items = [{"url": URL}]
            with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
                self.assertEqual(get_all_crawled_urls(name, url_items=items), set())  # 다시 수집 대상

            db[name].fail_update = None
            self.assertTrue(saver(db, [chunk("주문", "가"), chunk("이유", "나")]))
            self.assertNotIn("save_incomplete", db[name].docs[0]["metadata"])
            with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
                self.assertEqual(get_all_crawled_urls(name, url_items=items), {"900"})

    def test_complete_documents_are_skipped_in_update_mode(self):
        for saver, name, db in self.each_saver():
            saver(db, [chunk("주문", "가")])
            with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
                self.assertEqual(get_all_crawled_urls(name, url_items=[{"url": URL}]), {"900"})


if __name__ == "__main__":
    unittest.main()
