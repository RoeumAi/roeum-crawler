"""판례(case) 상세 수집의 파싱·저장 결과(content_hash 건너뛰기 포함)를 고정하는 특성 테스트."""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.case.logic import scraper as case_scraper
from scripts.core.database.source_versioning import sha256_content
from tests.fakes import FakeDB

URL = "https://www.law.go.kr/LSW/precInfoP.do?precSeq=900"
SUBTITLE = "대법원 2004. 3. 25. 선고 2003다67359 판결"
BODY = (
    "<h4>【판시사항】</h4><p>판시 내용</p>"
    "<h4>【원고, 상고인】</h4><h5>【원고】</h5><p>갑</p>"
    "<h4>【주문】</h4><p>상고를 기각한다</p>"
    "<h4>【이유】</h4><p>하나</p><h4>【이유】</h4><p>둘</p>"
)


def page(title="판례 제목", subtitle=SUBTITLE, body=BODY):
    h2 = f"<h2>{title}</h2>" if title is not None else ""
    sub = f'<div class="subtit1">[{subtitle}]</div>' if subtitle else ""
    return f'<div id="contentBody">{h2}{sub}<div id="conScroll">{body}</div></div>'


def run(html, db=None, url=URL, **kwargs):
    db = FakeDB() if db is None else db
    kwargs.setdefault("save_jsonl", False)
    resp = Mock()
    resp.text = html
    resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=resp), \
         patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
        result = asyncio.run(case_scraper.scrape_and_save(url, output_dir="", output_name="t", **kwargs))
    return result, db


def patch_split(replacement):
    """토큰 분할을 바꿔 끼운다. 분할 함수는 공용 모듈에 있고, 예전 case 모듈 이름도 함께 덮는다."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("scripts.core.database.section_chunks.split_section", replacement))
    stack.enter_context(patch.object(case_scraper, "split_section", replacement, create=True))
    return stack


def keyed(db):
    return {(d["doc_type"], d["chunk_seq"]): d for d in db["case"].docs}


class CaseParseTest(unittest.TestCase):
    def test_h5_party_headings_merge_into_parent_and_duplicate_titles_get_suffix(self):
        chunks = case_scraper.parse_case_law_content(BODY, "900", URL, "제목")
        self.assertEqual([c["title"] for c in chunks], ["판시사항", "원고, 상고인", "주문", "이유", "이유"])
        self.assertEqual([c["chunk_id"] for c in chunks],
                         ["doc:900:issue", "doc:900:plaintiff", "doc:900:order",
                          "doc:900:reasoning", "doc:900:reasoning_2"])
        self.assertEqual(chunks[1]["text"], "【원고】\n\n갑")

    def test_extract_judgment_date(self):
        self.assertEqual(case_scraper.extract_judgment_date(SUBTITLE), "2004-03-25")
        self.assertIsNone(case_scraper.extract_judgment_date("날짜 없음"))
        self.assertIsNone(case_scraper.extract_judgment_date(""))

    def test_clean_spaces_uses_fullwidth_bracket_rule(self):
        self.assertEqual(case_scraper.clean_spaces("［1］\n  본문 \xa0 줄"), "［1］ 본문 줄")
        # 반각 대괄호 뒤 줄바꿈은 합치지 않는다(헌재 계열과 다른 점)
        self.assertEqual(case_scraper.clean_spaces("[1]\n본문"), "[1]\n본문")


class CaseSaveTest(unittest.TestCase):
    def test_rows_carry_judgment_date_hash_and_sequence(self):
        result, db = run(page())
        self.assertEqual(result, {"doc_id": "900", "status": "success", "action": "insert"})
        rows = keyed(db)
        self.assertEqual(sorted(rows), [("원고, 상고인", 1), ("이유", 1), ("이유", 2), ("주문", 1), ("판시사항", 1)])
        row = rows[("이유", 2)]
        self.assertEqual(row["content"], "둘")
        self.assertEqual(row["chunk_id"], "case:900:이유:2")
        self.assertEqual((row["title"], row["sub_title"]), ("판례 제목", SUBTITLE))
        self.assertEqual((row["article_number"], row["article_title"]), ("2", "이유"))
        self.assertEqual(row["content_hash"], sha256_content("둘"))
        self.assertEqual(row["source_version_id"], f"case:case:900:이유:2:{sha256_content('둘')}")
        meta = row["metadata"]
        self.assertEqual(meta["effective"], "2004-03-25")
        self.assertEqual((meta["chunk_index"], meta["total_chunks"], meta["total_sub_chunks"]), (5, 5, 1))
        self.assertEqual((meta["source_url"], meta["source_type"], meta["is_active"], meta["chapter"]),
                         (URL, "web", True, "판례 제목"))
        self.assertTrue(meta["created_at"] and meta["updated_at"] and meta["token_count"] > 0)
        self.assertNotIn("last_check_at", meta)

    def test_effective_is_none_without_judgment_date(self):
        _, db = run(page(subtitle="날짜 없는 부제"))
        self.assertIsNone(db["case"].docs[0]["metadata"]["effective"])
        _, db = run(page(subtitle=None))
        self.assertEqual(db["case"].docs[0]["sub_title"], "")

    def test_unchanged_rerun_only_refreshes_last_check_and_titles(self):
        _, db = run(page())
        before = {k: (d["metadata"]["updated_at"], d["content_hash"], d["_id"]) for k, d in keyed(db).items()}
        result, db = run(page(title="바뀐 제목", subtitle="바뀐 부제"), db=db)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(db["case"].docs), 5)
        for key, row in keyed(db).items():
            self.assertEqual((row["metadata"]["updated_at"], row["content_hash"], row["_id"]), before[key])
            self.assertTrue(row["metadata"]["last_check_at"])
            self.assertEqual((row["title"], row["sub_title"]), ("바뀐 제목", "바뀐 부제"))
            self.assertEqual(row["metadata"]["is_active"], True)
        # 건너뛴 행은 부제가 바뀌어도 effective 를 다시 쓰지 않는다
        self.assertEqual(keyed(db)[("주문", 1)]["metadata"]["effective"], "2004-03-25")

    def test_changed_section_is_rewritten_and_others_untouched(self):
        _, db = run(page())
        first = keyed(db)[("주문", 1)]["metadata"]["updated_at"]
        changed = BODY.replace("상고를 기각한다", "원심을 파기한다")
        run(page(body=changed), db=db)
        rows = keyed(db)
        self.assertEqual(rows[("주문", 1)]["content"], "원심을 파기한다")
        self.assertEqual(rows[("주문", 1)]["content_hash"], sha256_content("원심을 파기한다"))
        self.assertNotIn("last_check_at", rows[("주문", 1)]["metadata"])
        self.assertTrue(rows[("판시사항", 1)]["metadata"]["last_check_at"])
        self.assertNotEqual(first, None)

    def test_skip_clears_incomplete_flag_and_reactivates(self):
        _, db = run(page())
        for d in db["case"].docs:
            d["metadata"]["save_incomplete"] = True
            d["metadata"]["is_active"] = False
        run(page(), db=db)
        for d in db["case"].docs:
            self.assertNotIn("save_incomplete", d["metadata"])
            self.assertTrue(d["metadata"]["is_active"])

    def test_missing_sections_are_deactivated_including_rows_without_flag(self):
        _, db = run(page())
        db["case"].insert_one({"doc_id": "900", "doc_type": "옛 섹션", "chunk_seq": 1, "metadata": {}})
        db["case"].insert_one({"doc_id": "900", "doc_type": "이미 꺼짐", "chunk_seq": 1, "metadata": {"is_active": False}})
        db["case"].insert_one({"doc_id": "901", "doc_type": "다른 문서", "chunk_seq": 1, "metadata": {"is_active": True}})
        run(page(body="<h4>【주문】</h4><p>상고를 기각한다</p>"), db=db)
        state = {(d["doc_id"], d["doc_type"], d["chunk_seq"]): d["metadata"].get("is_active") for d in db["case"].docs}
        self.assertEqual(state[("900", "주문", 1)], True)
        self.assertEqual(state[("900", "판시사항", 1)], False)
        self.assertEqual(state[("900", "이유", 2)], False)
        self.assertEqual(state[("900", "옛 섹션", 1)], False)
        self.assertEqual(state[("900", "이미 꺼짐", 1)], False)
        self.assertEqual(state[("901", "다른 문서", 1)], True)
        gone = [d for d in db["case"].docs if d["doc_type"] == "판시사항"][0]["metadata"]
        self.assertTrue(gone["deactivated_at"])

    def test_partial_failure_is_failed_marked_and_keeps_old_rows_active(self):
        _, db = run(page())
        db["case"].fail_update = lambda update, filter_: filter_.get("doc_type") == "주문"
        changed = BODY.replace("상고를 기각한다", "원심을 파기한다").replace("판시 내용", "새 판시")
        result, db = run(page(body=changed), db=db)
        self.assertEqual(result, {"doc_id": "900", "status": "failed", "error": "MongoDB save failed"})
        self.assertTrue(all(d["metadata"].get("save_incomplete") for d in db["case"].docs))
        self.assertTrue(all(d["metadata"]["is_active"] for d in db["case"].docs))
        self.assertEqual(keyed(db)[("판시사항", 1)]["content"], "새 판시")

    def test_split_section_failure_fails_document(self):
        with patch_split(Mock(side_effect=RuntimeError("tok"))):
            result, db = run(page())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(db["case"].docs, [])

    def test_long_section_rows_are_numbered_per_title(self):
        with patch_split(lambda text: [(text + "-a", 2), (text + "-b", 3)]):
            _, db = run(page(body="<h4>【이유】</h4><p>하나</p><h4>【이유】</h4><p>둘</p>"))
        self.assertEqual([(d["chunk_seq"], d["content"], d["metadata"]["total_sub_chunks"]) for d in db["case"].docs],
                         [(1, "하나-a", 2), (2, "하나-b", 2), (3, "둘-a", 2), (4, "둘-b", 2)])


class CaseScrapeOutcomeTest(unittest.TestCase):
    def test_missing_h2_fails_without_doc_id(self):
        result, db = run(page(title=None))
        self.assertEqual(result, {"doc_id": None, "status": "failed"})
        self.assertEqual(dict(db), {})

    def test_missing_doc_id_fails(self):
        result, _ = run(page(), url="https://www.law.go.kr/LSW/precInfoP.do?x=1")
        self.assertEqual(result, {"doc_id": None, "status": "failed"})

    def test_body_without_sections_saves_metadata_row_as_no_change(self):
        result, db = run(page(body="<p>머리 없는 본문</p>"))
        self.assertEqual(result, {"doc_id": "900", "status": "success", "action": "no_change"})
        self.assertEqual(db["case"].docs[0]["doc_type"], "메타데이터")

    def test_save_to_db_false_and_dict_item(self):
        result, db = run(page(), url={"url": URL}, save_to_db=False)
        self.assertEqual(result["status"], "success")
        self.assertEqual(dict(db), {})

    def test_fetch_error_reported(self):
        with patch("requests.get", side_effect=RuntimeError("boom")):
            result = asyncio.run(case_scraper.scrape_and_save(URL, "", "t", save_jsonl=False))
        self.assertEqual(result, {"doc_id": None, "status": "failed", "error": "RuntimeError: boom"})

    def test_jsonl_outputs_document_and_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp = Mock(text=page())
            resp.raise_for_status.return_value = None
            with patch("requests.get", return_value=resp):
                asyncio.run(case_scraper.scrape_and_save(URL, tmp, "out", save_to_db=False, save_jsonl=True))
            with open(os.path.join(tmp, "out_document.jsonl"), encoding="utf-8") as f:
                self.assertEqual(json.loads(f.read()),
                                 {"doc_id": "900", "title": "판례 제목", "subtitle": SUBTITLE, "source_url": URL})
            with open(os.path.join(tmp, "out_chunks.jsonl"), encoding="utf-8") as f:
                chunks = [json.loads(line) for line in f]
            self.assertEqual([c["chunk_id"] for c in chunks][-1], "doc:900:reasoning_2")


if __name__ == "__main__":
    unittest.main()
