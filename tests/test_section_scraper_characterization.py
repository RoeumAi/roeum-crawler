"""헌재결정례·법제처해석례·행정심판재결례 상세 수집의 파싱·저장 결과를 고정하는 특성 테스트.

세 컬렉션은 거의 같은 코드라 하나로 합칠 때 결과가 달라지지 않는지 이 테스트로 지킨다.
네트워크·실제 DB 없이 가짜 응답과 가짜 컬렉션으로 돌린다.
"""
import asyncio
import importlib
import unittest
from unittest.mock import Mock, patch

from scripts.core.database.source_versioning import sha256_content
from tests.fakes import FakeDB

# 컬렉션별로 다른 점: URL 파라미터, 섹션 이름, 부제 필수 여부, 섹션 키 매핑 결과(chunk_id 의 키)
SPECS = {
    "constitutional_decc": {
        "param": "detcSeq",
        "sections": [("판시 사항", "판시 내용"), ("결정요지", "첫 문단"), ("이유", "하나"), ("이유", "둘")],
        "keys": ["section_1", "summary", "reasoning", "reasoning_2"],
        "subtitle_required": True,
    },
    "legislation_expc": {
        "param": "expcSeq",
        "sections": [("질의요지", "질문 내용"), ("회답", "답변 내용"), ("이유", "하나"), ("관계 법령", "근로기준법")],
        "keys": ["query", "answer", "reasoning", "related_laws"],
        "subtitle_required": False,
    },
    "admin_decc": {
        "param": "deccSeq",
        "sections": [("재결요지", "요지 내용"), ("주문", "청구를 기각한다"), ("이유", "하나"), ("사건개요", "개요 내용")],
        "keys": ["summary", "order", "reasoning", "case_overview"],
        "subtitle_required": False,
    },
}
SUBTITLE = "전원재판부 2019헌바454, 2022. 10. 27."


def body_of(sections):
    return "".join(f"<h4>【{title}】</h4><p>{text}</p>" for title, text in sections)


def page(title, subtitle, body):
    h2 = f"<h2>{title}</h2>" if title is not None else ""
    sub = f'<div class="subtit1">[{subtitle}]</div>' if subtitle else ""
    return f'<div id="contentBody">{h2}{sub}<div id="conScroll">{body}</div></div>'


def response(html):
    resp = Mock()
    resp.text = html
    resp.raise_for_status.return_value = None
    return resp


def scraper_of(name):
    return importlib.import_module(f"scripts.{name}.logic.scraper")


def url_of(name, doc_id="900"):
    return f"https://www.law.go.kr/LSW/x.do?{SPECS[name]['param']}={doc_id}"


def run(name, html, url=None, db=None, **kwargs):
    db = FakeDB() if db is None else db
    kwargs.setdefault("save_jsonl", False)
    with patch("requests.get", return_value=response(html)), \
         patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
        result = asyncio.run(scraper_of(name).scrape_and_save(url or url_of(name), **kwargs))
    return result, db


class SectionScraperCharacterizationTest(unittest.TestCase):
    def each(self):
        for name, spec in SPECS.items():
            with self.subTest(collection=name):
                yield name, spec

    def test_parse_content_maps_section_keys_and_keeps_paragraphs(self):
        for name, spec in self.each():
            chunks = scraper_of(name).parse_content(body_of(spec["sections"]), "900", "u", "제목")
            self.assertEqual([c["title"] for c in chunks], [t for t, _ in spec["sections"]])
            self.assertEqual([c["chunk_id"] for c in chunks], [f"doc:900:{k}" for k in spec["keys"]])
            self.assertEqual([c["text"] for c in chunks], [t for _, t in spec["sections"]])
            self.assertEqual(chunks[0]["metadata"], {"chapter": "제목"})
            self.assertEqual(chunks[0]["source_url"], "u")

    def test_parse_content_without_sections_makes_metadata_chunk(self):
        for name, _ in self.each():
            chunks = scraper_of(name).parse_content("<p>머리 없는 본문</p>", "900", "u", "제목")
            self.assertEqual(chunks, [{
                "chunk_id": "doc:900:metadata", "doc_id": "900", "title": "메타데이터",
                "text": "제목: 제목\n출처: u",
                "metadata": {"chapter": "제목", "is_metadata_only": True}, "source_url": "u",
            }])

    def test_parse_content_joins_top_level_text_and_cleans_spaces(self):
        for name, spec in self.each():
            title = spec["sections"][0][0]
            html = f"<h4>【{title}】</h4>앞 글\xa0\xa0 뒤<p>문단</p>"
            chunks = scraper_of(name).parse_content(html, "1", "u", "t")
            self.assertEqual(chunks[0]["text"], "앞 글 뒤 문단".replace(" 문단", "\n\n문단"))

    def test_get_doc_id_from_url_reads_own_parameter_only(self):
        for name, spec in self.each():
            module = scraper_of(name)
            self.assertEqual(module.get_doc_id_from_url(url_of(name, "77")), "77")
            other = "otherSeq=5" if spec["param"] != "otherSeq" else "x=5"
            self.assertIsNone(module.get_doc_id_from_url(f"https://www.law.go.kr/x.do?{other}"))

    def test_clean_spaces_is_shared_behavior(self):
        for name, _ in self.each():
            clean = scraper_of(name).clean_spaces
            self.assertEqual(clean(None), "")
            self.assertEqual(clean("[1]\n  본문 \xa0 줄\n\n\n끝  "), "[1] 본문 줄\n\n끝")

    def test_saved_rows_per_section(self):
        for name, spec in self.each():
            html = page("제목  입니다", SUBTITLE, body_of(spec["sections"]))
            result, db = run(name, html)
            self.assertEqual(result, {"doc_id": "900", "status": "success", "action": "insert"})
            rows = db[name].docs
            seqs = {}
            expected = []
            for title, text in spec["sections"]:
                seqs[title] = seqs.get(title, 0) + 1
                expected.append((title, seqs[title], text))
            self.assertEqual([(r["doc_type"], r["chunk_seq"], r["content"]) for r in rows], expected)
            for row, (title, seq, text) in zip(rows, expected):
                self.assertEqual(row["chunk_id"], f"{name}:900:{title.replace(' ', '_')}:{seq}")
                self.assertEqual(row["doc_id"], "900")
                self.assertEqual(row["title"], "제목 입니다")
                self.assertEqual(row["sub_title"], SUBTITLE)
                self.assertEqual(row["article_number"], str(seq))
                self.assertEqual(row["article_title"], title)
                self.assertEqual(row["content_hash"], sha256_content(text))
                self.assertEqual(row["source_version_id"], f"{name}:{row['chunk_id']}:{sha256_content(text)}")
                meta = row["metadata"]
                self.assertEqual(meta["source_url"], url_of(name))
                self.assertEqual((meta["source_type"], meta["is_active"], meta["chapter"]), ("web", True, "제목 입니다"))
                self.assertEqual((meta["total_chunks"], meta["total_sub_chunks"]), (4, 1))
                self.assertNotIn("effective", meta)
                self.assertNotIn("save_incomplete", meta)
                self.assertTrue(meta["created_at"] and meta["updated_at"])
            # 다른 컬렉션 이름으로는 아무것도 쓰지 않는다
            self.assertEqual([k for k in db if db[k].docs], [name])

    def test_resave_keeps_row_count_and_created_at(self):
        for name, spec in self.each():
            html = page("제목", SUBTITLE, body_of(spec["sections"]))
            _, db = run(name, html)
            created = [r["metadata"]["created_at"] for r in db[name].docs]
            result, db = run(name, html, db=db)
            self.assertEqual(result["status"], "success")
            self.assertEqual(len(db[name].docs), 4)
            self.assertEqual([r["metadata"]["created_at"] for r in db[name].docs], created)

    def test_missing_subtitle_policy(self):
        for name, spec in self.each():
            html = page("제목", None, body_of(spec["sections"]))
            result, db = run(name, html)
            if spec["subtitle_required"]:
                self.assertEqual(result, {
                    "doc_id": "900", "status": "failed",
                    "error": "Constitutional decision subtitle extraction failed",
                })
                self.assertEqual(db[name].docs, [])
            else:
                self.assertEqual(result["status"], "success")
                self.assertEqual({r["sub_title"] for r in db[name].docs}, {""})

    def test_title_falls_back_to_other_headings_then_url(self):
        for name, spec in self.each():
            body = body_of(spec["sections"][:1])
            html = f'<div id="contentBody"><div class="tit_doc"> 대체  제목 </div><div class="subtit1">[{SUBTITLE}]</div><div id="conScroll">{body}</div></div>'
            _, db = run(name, html)
            self.assertEqual(db[name].docs[0]["title"], "대체 제목")
            html = f'<div id="contentBody"><div class="subtit1">[{SUBTITLE}]</div><div id="conScroll">{body}</div></div>'
            _, db = run(name, html)
            self.assertEqual(db[name].docs[0]["title"], url_of(name))

    def test_missing_doc_id_is_failed_without_save(self):
        for name, spec in self.each():
            html = page("제목", SUBTITLE, body_of(spec["sections"]))
            result, db = run(name, html, url="https://www.law.go.kr/LSW/x.do?nothing=1")
            self.assertEqual(result, {"doc_id": None, "status": "failed"})
            self.assertEqual(db[name].docs, [])

    def test_dict_url_item_is_accepted(self):
        for name, spec in self.each():
            html = page("제목", SUBTITLE, body_of(spec["sections"]))
            result, db = run(name, html, url={"url": url_of(name, "5"), "name": "x"})
            self.assertEqual(result["doc_id"], "5")
            self.assertEqual(len(db[name].docs), 4)

    def test_save_to_db_false_writes_nothing(self):
        for name, spec in self.each():
            html = page("제목", SUBTITLE, body_of(spec["sections"]))
            result, db = run(name, html, save_to_db=False)
            self.assertEqual(result["status"], "success")
            self.assertEqual(dict(db), {})

    def test_body_without_sections_saves_metadata_row_and_reports_no_change(self):
        for name, _ in self.each():
            html = page("제목", SUBTITLE, "<p>머리 없는 본문</p>")
            result, db = run(name, html)
            self.assertEqual(result, {"doc_id": "900", "status": "success", "action": "no_change"})
            row = db[name].docs[0]
            self.assertEqual((row["doc_type"], row["content"]), ("메타데이터", f"제목: 제목\n출처: {url_of(name)}"))
            self.assertTrue(row["metadata"]["is_metadata_only"])

    def test_old_metadata_row_is_deactivated_when_real_body_arrives(self):
        for name, spec in self.each():
            _, db = run(name, page("제목", SUBTITLE, "<p>머리 없는 본문</p>"))
            run(name, page("제목", SUBTITLE, body_of(spec["sections"])), db=db)
            by_type = {r["doc_type"]: r["metadata"]["is_active"] for r in db[name].docs}
            self.assertFalse(by_type["메타데이터"])
            self.assertTrue(all(v for k, v in by_type.items() if k != "메타데이터"))

    def test_partial_save_failure_is_failed_and_marked(self):
        for name, spec in self.each():
            db = FakeDB()
            db[name].fail_update = lambda update, filter_: filter_.get("doc_type") == "이유" and filter_.get("chunk_seq") == 1
            result, db = run(name, page("제목", SUBTITLE, body_of(spec["sections"])), db=db)
            self.assertEqual(result, {"doc_id": "900", "status": "failed", "error": "MongoDB save failed"})
            self.assertTrue(all(r["metadata"].get("save_incomplete") for r in db[name].docs))

    def test_fetch_error_is_reported_with_type_and_message(self):
        for name, _ in self.each():
            with patch("requests.get", side_effect=RuntimeError("boom")):
                result = asyncio.run(scraper_of(name).scrape_and_save(url_of(name), save_jsonl=False))
            self.assertEqual(result, {"doc_id": None, "status": "failed", "error": "RuntimeError: boom"})

    def test_long_section_is_split_into_numbered_rows(self):
        for name, spec in self.each():
            title = spec["sections"][0][0]
            html = page("제목", SUBTITLE, body_of([(title, "본문")]))
            with patch("scripts.core.database.section_chunks.split_section",
                       lambda text: [("앞", 3), ("뒤", 4)]):
                _, db = run(name, html)
            self.assertEqual([(r["chunk_seq"], r["content"], r["metadata"]["token_count"],
                               r["metadata"]["total_sub_chunks"]) for r in db[name].docs],
                             [(1, "앞", 3, 2), (2, "뒤", 4, 2)])


if __name__ == "__main__":
    unittest.main()
