"""법령·행정규칙 스크래퍼 특성 테스트: 리팩토링 전후로 같은 입력에 같은 결과·저장 문서·로그가 나오는지 고정한다.

네트워크·실제 DB·브라우저 없이 가짜 저장소와 가짜 Playwright 로 돌린다.
"""
import contextvars
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

from scripts.adrule.logic import scraper as adrule_scraper
from scripts.law.logic import scraper as law_scraper

TODAY = datetime.now().strftime("%Y-%m-%d")
URL = "https://example.invalid/doc"

SPLIT_FUNCTIONS = [("law", law_scraper.split_articles_by_number),
                   ("adrule", adrule_scraper.split_articles_by_number)]


# ---------------------------------------------------------------- 조문 분리

class SplitArticlesTest(unittest.TestCase):
    def each(self, text):
        results = {name: fn(text) for name, fn in SPLIT_FUNCTIONS}
        self.assertEqual(results["law"], results["adrule"])
        return results["law"]

    def test_plain_articles(self):
        got = self.each("[제1조(목적)]\n이 법은 목적을 정한다.\n\n[제2조(정의)]\n뜻은 다음과 같다.\n① 가\n② 나")
        self.assertEqual(got, [
            {"article_number": "1", "article_number_numeric": 1.0, "article_title": "제1조(목적)",
             "content": "이 법은 목적을 정한다."},
            {"article_number": "2", "article_number_numeric": 2.0, "article_title": "제2조(정의)",
             "content": "뜻은 다음과 같다.\n① 가\n② 나"},
        ])

    def test_branch_numbers(self):
        got = self.each("[제5조(본조)]\n가\n\n[제5조의1(정의)]\n나\n\n[제5조의10(열)]\n다")
        self.assertEqual([a["article_number"] for a in got], ["5", "5.1", "5.10"])
        self.assertEqual([a["article_number_numeric"] for a in got], [5.0, 5.1, 5.1])
        self.assertEqual([a["article_title"] for a in got], ["제5조(본조)", "제5조의1(정의)", "제5조의10(열)"])

    def test_text_before_first_article_is_dropped(self):
        got = self.each("서문입니다\n\n[제1조(목적)]\n본문")
        self.assertEqual([a["article_number"] for a in got], ["1"])

    def test_title_without_parentheses_does_not_match(self):
        self.assertEqual(self.each("[제1조]\n본문"), [])
        self.assertEqual(self.each("조문 표기가 없는 본문"), [])
        self.assertEqual(self.each(""), [])

    def test_duplicate_numbers_are_both_returned_in_order(self):
        got = self.each("[제1조(목적)]\n본칙\n\n[제1조(시행일)]\n부칙")
        self.assertEqual([(a["article_number"], a["content"]) for a in got], [("1", "본칙"), ("1", "부칙")])

    def test_trailing_label_is_merged_into_last_article(self):
        got = self.each("[제1조(목적)]\n본칙\n\n부칙\n이 법은 공포한 날부터 시행한다.")
        self.assertEqual(got[0]["content"], "본칙\n\n부칙\n이 법은 공포한 날부터 시행한다.")


# ---------------------------------------------------------------- 법령 HTML 파싱

LAW_HTML = (
    '<div id="conTop"><h2> 근로기준법 </h2></div>'
    '<div class="ct_sub">[시행 2026. 1. 5.] [법률 제1호, 2025. 1. 1., 일부개정]</div>'
    '<div id="contentBody">'
    '<div class="pgroup"><p class="gtit">제1장 총칙</p><span class="bl"><label>제1조(목적)</label></span>'
    '<p>이 법은 <a href="#">근로조건</a>의 기준을 정한다.</p></div>'
    '<div class="pgroup"><span class="bl"><label>제2조(정의)</label></span>'
    '<p>① 정의는 다음과 같다.</p><p>1. 근로자</p></div>'
    '<div class="pgroup"><span class="bl"><label>전문</label></span><p>번호 없는 항목</p></div>'
    '</div>'
)
LAW_URL = "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=100"


class LawParseTest(unittest.TestCase):
    def test_structured_law_becomes_unified_doc(self):
        doc = law_scraper.parse_law_html(LAW_HTML, LAW_URL)
        self.assertEqual(doc, {
            "doc_id": "100", "doc_type": "법령", "title": "근로기준법",
            "sub_title": "법률 제1호, 2025. 1. 1., 일부개정",
            "content": ("[제1조(목적)]\n제1장 총칙\n제1조(목적)이 법은 근로조건의 기준을 정한다.\n\n"
                        "[제2조(정의)]\n제2조(정의)① 정의는 다음과 같다.\n1. 근로자"),
            "metadata": {"source_url": LAW_URL, "source_type": "web", "effective": "2026-01-05",
                         "created_at": TODAY, "is_active": True},
        })

    def test_missing_doc_id_title_or_body_returns_none(self):
        self.assertIsNone(law_scraper.parse_law_html(LAW_HTML, "https://www.law.go.kr/x?a=1"))
        self.assertIsNone(law_scraper.parse_law_html(LAW_HTML.replace("<h2> 근로기준법 </h2>", "<h2></h2>"), LAW_URL))
        self.assertIsNone(law_scraper.parse_law_html("<div id='conTop'><h2>t</h2></div>", LAW_URL))

    def test_short_law_without_articles_uses_whole_text(self):
        html = '<div id="conTop"><h2>단문법</h2></div><div id="contentBody"> 첫 줄 \n\n 둘째 줄 </div>'
        doc = law_scraper.parse_law_html(html, LAW_URL.replace("lsiSeq", "lsId"))
        self.assertEqual(doc["content"], "첫 줄\n둘째 줄")
        self.assertEqual(doc["sub_title"], "")
        self.assertEqual(doc["metadata"]["effective"], "")

    def test_empty_body_returns_none(self):
        html = '<div id="conTop"><h2>단문법</h2></div><div id="contentBody">  </div>'
        self.assertIsNone(law_scraper.parse_law_html(html, LAW_URL))


# ---------------------------------------------------------------- 행정규칙 HTML 파싱

ADRULE_STRUCTURED = (
    '<div class="pgroup"><p class="gtit">제1장 총칙</p><span class="bl"><label>제1조(목적)</label></span>'
    '<div class="lawcon"><p>이 고시는 <a href="#">목적</a>을 정한다.</p><p>두 번째 줄</p></div></div>'
    '<div class="pgroup"><span class="bl"><label>제2조(정의)</label></span>'
    '<div class="lawcon">정의 본문</div></div>'
    '<div class="pgroup"><span class="bl"><label>제3조(빈)</label></span></div>'
    '<div class="pgroup"><span class="bl"><label>머리말</label></span><div class="lawcon">x</div></div>'
)
ADRULE_UNSTRUCTURED = (
    '<div class="lawcon"><p>서문 한 줄</p><p>Ⅰ. 개요</p><p>개요 본문</p><p>Ⅱ. 기준</p><p>기준 본문</p></div>'
)
ADRULE_PDF_PAGE = '<iframe class="fancybox-iframe" src="/LSW/viewer/skin/doc.html?key=777&amp;x=1"></iframe>'


def adrule_page(outer_html, inner_html=""):
    page = Mock()
    page.content = AsyncMock(return_value=outer_html)
    locator = Mock()
    locator.inner_html = AsyncMock(return_value=inner_html)
    page.locator = Mock(return_value=locator)
    return page


class AdruleParseTest(unittest.IsolatedAsyncioTestCase):
    async def test_structured(self):
        outer = '<div class="pgroup"><span class="bl"><label>제1조(목적)</label></span></div>'
        chunks = await adrule_scraper.parse_law_html(adrule_page(outer, ADRULE_STRUCTURED), "d1", "고시", URL, "o")
        self.assertEqual(chunks, [
            {"chunk_id": "doc:d1:article_1", "doc_id": "d1", "title": "제1조(목적)",
             "text": "이 고시는 목적을 정한다.두 번째 줄", "metadata": {"chapter": "제1장 총칙"}, "source_url": URL},
            {"chunk_id": "doc:d1:article_2", "doc_id": "d1", "title": "제2조(정의)",
             "text": "정의 본문", "metadata": {"chapter": "제1장 총칙"}, "source_url": URL},
        ])

    async def test_unstructured(self):
        chunks = await adrule_scraper.parse_law_html(adrule_page("<div>본문</div>", ADRULE_UNSTRUCTURED), "d2", "고시", URL, "o")
        self.assertEqual([(c["chunk_id"], c["title"], c["text"]) for c in chunks], [
            ("doc:d2:preamble", "고시", "서문 한 줄"),
            ("doc:d2:section_1", "Ⅰ. 개요", "개요 본문"),
            ("doc:d2:section_2", "Ⅱ. 기준", "기준 본문"),
        ])
        self.assertEqual(chunks[1]["metadata"], {"chapter": "고시"})

    async def test_pdf_viewer_uses_downloaded_file(self):
        extracted = {"structured_text": " 본문 표 ", "extraction_status": "complete"}
        with patch.object(adrule_scraper, "_download_and_extract", return_value=extracted) as download:
            chunks = await adrule_scraper.parse_law_html(adrule_page(ADRULE_PDF_PAGE), "d3", "고시", URL, "o")
        download.assert_called_once_with("777")
        self.assertEqual(chunks, [{
            "chunk_id": "doc:d3:file_1", "doc_id": "d3", "title": "", "text": "본문 표",
            "metadata": {"chapter": "고시", "source_type": "pdf", "source_file_key": "777",
                         "pdf_extraction_status": "complete"},
            "source_url": URL,
        }])

    async def test_pdf_viewer_with_several_files_numbers_titles(self):
        page = adrule_page('<iframe class="fancybox-iframe" src="/a?key=1"></iframe>'
                           '<iframe class="fancybox-iframe" src="/a?key=2"></iframe>')
        with patch.object(adrule_scraper, "_download_and_extract",
                          return_value={"structured_text": "t", "extraction_status": "partial"}):
            chunks = await adrule_scraper.parse_law_html(page, "d", "고시", URL, "o")
        self.assertEqual([c["title"] for c in chunks], ["첨부 1", "첨부 2"])
        self.assertEqual([c["chunk_id"] for c in chunks], ["doc:d:file_1", "doc:d:file_2"])

    async def test_pdf_viewer_debug_writes_markdown_under_project_root(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(adrule_scraper, "project_root", tmp), \
             patch.object(adrule_scraper, "_download_and_extract",
                          return_value={"structured_text": "md 본문", "extraction_status": "complete"}):
            await adrule_scraper.parse_law_html(adrule_page(ADRULE_PDF_PAGE), "d", "고시", URL, "out", debug=True)
            with open(os.path.join(tmp, "debug", "out", "out_file_777.md"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "md 본문")


# ---------------------------------------------------------------- 원본 파일 내려받기·추출

class AdruleDownloadExtractTest(unittest.TestCase):
    def response(self, headers=None):
        return Mock(content=b"%PDF", headers=headers or {}, raise_for_status=Mock())

    def test_filename_comes_from_content_disposition_and_result_is_usable_document(self):
        response = self.response({"Content-Disposition": 'attachment; filename="%EA%B3%A0%EC%8B%9C.pdf"'})
        parsed = {"is_success": True, "extraction_status": "complete", "structured_text": "본문", "full_text": "본문"}
        with patch("requests.get", return_value=response) as get, \
             patch("scripts.utils.document_extraction._post_extract_text", return_value=parsed) as post:
            result = adrule_scraper._download_and_extract("555")
        self.assertEqual(get.call_args.args[0], "https://www.law.go.kr/LSW/flDownload.do?flSeq=555")
        self.assertEqual(get.call_args.kwargs["timeout"], 60)
        self.assertEqual(get.call_args.kwargs["headers"], {"User-Agent": "Mozilla/5.0"})
        self.assertEqual(post.call_args.args[1:], (b"%PDF", "고시.pdf"))
        self.assertEqual(result["structured_text"], "본문")

    def test_default_filename_when_no_header(self):
        parsed = {"is_success": True, "extraction_status": "complete", "structured_text": "본문"}
        with patch("requests.get", return_value=self.response()), \
             patch("scripts.utils.document_extraction._post_extract_text", return_value=parsed) as post:
            adrule_scraper._download_and_extract("555")
        self.assertEqual(post.call_args.args[1:], (b"%PDF", "555.pdf"))

    def test_unusable_text_raises(self):
        with patch("requests.get", return_value=self.response()), \
             patch("scripts.utils.document_extraction._post_extract_text", return_value={"is_success": False}):
            with self.assertRaises(ValueError):
                adrule_scraper._download_and_extract("555")

    def test_http_error_is_raised(self):
        response = Mock(content=b"", headers={}, raise_for_status=Mock(side_effect=RuntimeError("404")))
        with patch("requests.get", return_value=response):
            with self.assertRaises(RuntimeError):
                adrule_scraper._download_and_extract("555")


# ---------------------------------------------------------------- 저장 루프

class FakeRepo:
    """upsert 가 돌려줄 action 을 순서대로 정해 두는 가짜 저장소."""

    def __init__(self, actions=None, fail_on=(), mark_raises=False):
        self.actions = list(actions or [])
        self.fail_on = set(fail_on)
        self.mark_raises = mark_raises
        self.saved = []
        self.marks = []

    def upsert_with_change_detection(self, doc):
        n = len(self.saved) + 1
        self.saved.append(doc)
        if n in self.fail_on:
            raise RuntimeError("write failed")
        action = self.actions.pop(0) if self.actions else "insert"
        return f"saved-{n}", {"action": action, "version": 3, "changed_fields": ["content"] if action == "new_version" else []}

    def mark_save_complete(self, source_url, complete):
        if self.mark_raises:
            raise RuntimeError("mark failed")
        self.marks.append((source_url, complete))


TEXT = "[제1조(목적)]\n이 법은 목적을 정한다.\n[제2조(정의)]\n뜻은 다음과 같다."


def law_doc(**extra):
    doc = {"doc_id": "law-1", "title": "테스트법", "sub_title": "부제", "content": TEXT,
           "metadata": {"source_url": URL, "source_type": "web", "effective": "2026-01-05",
                        "created_at": "2026-01-01", "is_active": True}}
    doc.update(extra)
    return doc


def run_law_save(repo, doc=None, record=None, annex=None, **kwargs):
    patches = [patch("scripts.core.database.mongo_client.get_mongo_db", return_value=Mock()),
               patch.object(law_scraper, "UnifiedDocumentRepository", return_value=repo),
               patch.object(law_scraper, "fetch_record", return_value={} if record is None else record),
               patch.object(law_scraper, "logger")]
    if annex is not None:
        patches.append(patch.object(law_scraper, "collect_annex_articles", return_value=annex))
    for p in patches:
        mock = p.start()
        if p.attribute == "logger":
            logger = mock
    try:
        result = law_scraper.save_to_mongodb(law_doc() if doc is None else doc, **kwargs)
    finally:
        patch.stopall()
    return result, logger


def run_adrule_save(repo, chunks=None, annex=None, record=None, **kwargs):
    patches = [patch("scripts.core.database.mongo_client.get_mongo_db", return_value=Mock()),
               patch.object(adrule_scraper, "UnifiedDocumentRepository", return_value=repo),
               patch.object(adrule_scraper, "fetch_record", return_value={}),
               patch.object(adrule_scraper, "logger")]
    if annex is not None:
        patches.append(patch.object(adrule_scraper, "collect_annex_articles", return_value=annex))
    for p in patches:
        mock = p.start()
        if p.attribute == "logger":
            logger = mock
    try:
        args = (chunks if chunks is not None else [{"title": "본문", "text": TEXT}], "테스트 고시", "ad-1", URL)
        result = adrule_scraper.save_to_mongodb(*args, lawgokr_record=record, **kwargs)
    finally:
        patch.stopall()
    return result, logger


def messages(mock):
    return [c.args[0] for c in mock.call_args_list]


class LawSaveLoopTest(unittest.TestCase):
    def test_new_version_and_documents_written(self):
        repo = FakeRepo(["insert", "new_version"])
        record = {"법령": {"기본정보": {"법령ID": "L1"}}}
        result, logger = run_law_save(repo, doc=law_doc(dept_name="고용노동부", is_upcoming=True), record=record,
                                      dept_code="1492000")
        self.assertEqual(result, "new_version")
        self.assertEqual(repo.marks, [(URL, True)])
        first, second = repo.saved
        self.assertEqual(first, {
            "chunk_id": "law:law-1:article:1", "doc_id": "law-1", "doc_type": "법령",
            "article_number": "1", "article_number_numeric": 1.0, "article_title": "제1조(목적)",
            "title": "테스트법", "sub_title": "부제", "content": "이 법은 목적을 정한다.",
            "metadata": {"source_url": URL, "source_type": "web", "effective": "2026-01-05",
                         "created_at": "2026-01-01", "is_active": True,
                         "article_index": 1, "total_articles": 2,
                         "dept_code": "1492000", "dept_name": "고용노동부", "law_id": "L1", "is_upcoming": True},
        })
        self.assertEqual(second["article_number"], "2")
        self.assertEqual(second["metadata"]["article_index"], 2)
        self.assertEqual(messages(logger.info), [
            "💾 신규 저장: saved-1 (v3)",
            "✅ 새 버전 생성: saved-2 (v3) - 변경사항: content",
            "📊 MongoDB 저장 완료: 성공 2개, 실패 0개",
        ])

    def test_update_existing_when_nothing_changed(self):
        repo = FakeRepo(["update_existing", "update_existing"])
        result, logger = run_law_save(repo)
        self.assertEqual(result, "update_existing")
        self.assertEqual(messages(logger.info)[0], "🔄 기존 버전 유지: saved-1 (v3) - 메타데이터만 업데이트")
        self.assertNotIn("dept_code", repo.saved[0]["metadata"])
        self.assertNotIn("law_id", repo.saved[0]["metadata"])
        self.assertFalse(repo.saved[0]["metadata"]["is_upcoming"])

    def test_title_based_law_id_when_api_has_none(self):
        repo = FakeRepo()
        result, logger = run_law_save(repo, doc=law_doc(law_id="TITLE"))
        self.assertEqual(repo.saved[0]["metadata"]["law_id"], "TITLE")
        self.assertIn("법령ID API 조회 실패, title 기반 law_id로 대체: doc_id=law-1", messages(logger.warning))

    def test_one_failure_is_empty_string_with_incomplete_mark(self):
        repo = FakeRepo(fail_on=[1])
        result, logger = run_law_save(repo)
        self.assertEqual(result, "")
        self.assertEqual(repo.marks, [(URL, False)])
        self.assertEqual(messages(logger.error), ["❌ 조문 저장 실패 (제1조(목적)): write failed"])
        self.assertIn("📊 MongoDB 저장 완료: 성공 1개, 실패 1개", messages(logger.info))

    def test_mark_failure_counts_as_failure(self):
        repo = FakeRepo(mark_raises=True)
        result, logger = run_law_save(repo)
        self.assertEqual(result, "")
        self.assertEqual(messages(logger.error), ["저장 완료 표시 실패: mark failed"])

    def test_annex_failure_count_starts_failed_count(self):
        repo = FakeRepo()
        result, _ = run_law_save(repo, annex=([], 1))
        self.assertEqual(result, "")
        self.assertEqual(len(repo.saved), 2)
        self.assertEqual(repo.marks, [(URL, False)])

    def test_annex_rows_get_annex_metadata_and_continue_index(self):
        repo = FakeRepo()
        row = {"article_number": "별표1", "article_number_numeric": None, "article_title": "[별표 1]", "content": "| a |",
               "annex": {"source_file": "a.pdf", "pdf_extraction_status": "complete"}}
        result, _ = run_law_save(repo, annex=([row], 0))
        self.assertEqual(result, "new_version")
        last = repo.saved[-1]
        self.assertEqual(last["chunk_id"], "law:law-1:article:별표1")
        self.assertEqual((last["metadata"]["article_index"], last["metadata"]["total_articles"]), (3, 3))
        self.assertEqual(last["metadata"]["annex_source_file"], "a.pdf")

    def test_no_articles_falls_back_to_single_whole_document(self):
        repo = FakeRepo()
        result, logger = run_law_save(repo, doc=law_doc(content="조문 표기 없는 짧은 본문"))
        self.assertEqual(result, "new_version")
        self.assertEqual(len(repo.saved), 1)
        saved = repo.saved[0]
        self.assertEqual((saved["article_number"], saved["article_number_numeric"], saved["article_title"]),
                         (None, None, "테스트법"))
        self.assertEqual(saved["chunk_id"], "law:law-1:article:1")
        self.assertIn("분리된 조문이 없습니다. 통합 문서 그대로 저장합니다.", messages(logger.warning))

    def test_empty_document_and_outer_error_return_false(self):
        result, logger = run_law_save(FakeRepo(), doc={})
        self.assertIs(result, False)
        self.assertEqual(messages(logger.error), ["저장할 문서가 없습니다."])
        result, logger = run_law_save(FakeRepo(), doc={"doc_id": "x"})  # content 키 없음 → 바깥 except
        self.assertIs(result, False)
        self.assertTrue(messages(logger.error)[0].startswith("MongoDB 저장 중 오류 발생: "))


class AdruleSaveLoopTest(unittest.TestCase):
    def test_new_version_and_documents_written(self):
        repo = FakeRepo(["insert", "new_version"])
        result, logger = run_adrule_save(repo, dept_code="D", sub_title="부제", effective_date="2026-02-01",
                                         dept_name="고용노동부", is_upcoming=True, adrule_id="A1")
        self.assertEqual(result, "new_version")
        self.assertEqual(repo.marks, [(URL, True)])
        self.assertEqual(repo.saved[0], {
            "chunk_id": "adrule:ad-1:article:1", "doc_id": "ad-1", "article_number": "1",
            "article_title": "제1조(목적)", "title": "테스트 고시", "sub_title": "부제",
            "content": "이 법은 목적을 정한다.", "doc_type": "행정규칙",
            "metadata": {"source_url": URL, "source_type": "web", "effective": "2026-02-01", "created_at": TODAY,
                         "article_index": 1, "total_articles": 2, "is_upcoming": True,
                         "dept_code": "D", "dept_name": "고용노동부", "adrule_id": "A1"},
        })
        self.assertNotIn("article_number_numeric", repo.saved[0])
        self.assertEqual(messages(logger.info), [
            "💾 신규 저장: ad-1 (article_number: 1)",
            "✅ 새 버전 생성: ad-1 (article_number: 2)",
            "📊 MongoDB 저장 완료: 성공 2개, 실패 0개",
        ])

    def test_update_existing_and_default_metadata(self):
        repo = FakeRepo(["update_existing", "update_existing"])
        result, logger = run_adrule_save(repo)
        self.assertEqual(result, "update_existing")
        self.assertEqual(messages(logger.info)[0], "🔄 기존 버전 유지: ad-1 (article_number: 1)")
        meta = repo.saved[0]["metadata"]
        self.assertEqual((meta["effective"], meta["is_upcoming"]), ("", False))
        for key in ("dept_code", "dept_name", "adrule_id"):
            self.assertNotIn(key, meta)
        self.assertEqual(repo.saved[0]["sub_title"], "")

    def test_failures(self):
        repo = FakeRepo(fail_on=[2])
        result, logger = run_adrule_save(repo)
        self.assertEqual(result, "")
        self.assertEqual(repo.marks, [(URL, False)])
        self.assertEqual(messages(logger.error), ["❌ 조문 저장 실패 (제2조(정의)): write failed"])
        repo = FakeRepo(mark_raises=True)
        result, logger = run_adrule_save(repo)
        self.assertEqual(result, "")
        self.assertEqual(messages(logger.error), ["저장 완료 표시 실패: mark failed"])

    def test_annex_failure_and_rows(self):
        result, _ = run_adrule_save(FakeRepo(), annex=([], 1))
        self.assertEqual(result, "")
        repo = FakeRepo()
        row = {"article_number": "별표1", "article_number_numeric": None, "article_title": "[별표 1]", "content": "| a |",
               "annex": {"source_file": "a.pdf", "pdf_extraction_status": "complete"}}
        result, _ = run_adrule_save(repo, annex=([row], 0))
        self.assertEqual(result, "new_version")
        self.assertEqual(repo.saved[-1]["article_number"], "별표1")
        self.assertTrue(repo.saved[-1]["metadata"]["is_annex"])

    def test_no_articles_fallback_numbers_by_position(self):
        repo = FakeRepo()
        result, logger = run_adrule_save(repo, chunks=[{"title": "", "text": "조문 표기 없는 본문"}, "문자열 청크"])
        self.assertEqual(result, "new_version")
        saved = repo.saved[0]
        self.assertEqual((saved["article_number"], saved["article_title"], saved["chunk_id"]),
                         ("1", None, "adrule:ad-1:article:1"))
        self.assertEqual(saved["content"], "조문 표기 없는 본문\n\n문자열 청크")
        self.assertIn("분리된 조문이 없습니다. 통합 문서 그대로 저장합니다.", messages(logger.warning))

    def test_empty_chunks_return_false(self):
        result, logger = run_adrule_save(FakeRepo(), chunks=[])
        self.assertIs(result, False)
        self.assertEqual(messages(logger.warning), ["저장할 청크가 없습니다."])

    def test_pdf_file_metadata_partial_warning(self):
        repo = FakeRepo()
        chunks = [{"title": "", "text": "본문", "metadata": {"source_file_key": "1", "pdf_extraction_status": "complete"}},
                  {"title": "", "text": "둘째", "metadata": {"source_file_key": "2", "pdf_extraction_status": "partial"}}]
        result, logger = run_adrule_save(repo, chunks=chunks)
        meta = repo.saved[0]["metadata"]
        self.assertEqual((meta["source_type"], meta["source_file_keys"], meta["pdf_extraction_status"]),
                         ("pdf", ["1", "2"], "partial"))
        self.assertEqual(messages(logger.warning)[0], "⚠️ ad-1: 원본 파일 일부 페이지를 읽지 못함(partial) — 읽은 부분만 저장")

    def test_record_is_fetched_when_not_given(self):
        patches = [patch("scripts.core.database.mongo_client.get_mongo_db", return_value=Mock()),
                   patch.object(adrule_scraper, "UnifiedDocumentRepository", return_value=FakeRepo()),
                   patch.object(adrule_scraper, "fetch_record", return_value={}) ]
        mocks = [p.start() for p in patches]
        try:
            adrule_scraper.save_to_mongodb([{"title": "t", "text": TEXT}], "t", "ad-9", URL)
        finally:
            patch.stopall()
        mocks[2].assert_called_once_with("admrul", "ad-9")


# ---------------------------------------------------------------- 비동기 래퍼

class AsyncWrapperTest(unittest.IsolatedAsyncioTestCase):
    async def test_law_passes_positional_args_and_returns_result(self):
        with patch.object(law_scraper, "save_to_mongodb", return_value="new_version") as save:
            result = await law_scraper.save_to_mongodb_async({"doc_id": "x"}, "D1")
        self.assertEqual(result, "new_version")
        save.assert_called_once_with({"doc_id": "x"}, "D1")

    async def test_law_exception_returns_empty_string(self):
        with patch.object(law_scraper, "save_to_mongodb", side_effect=RuntimeError("x")), \
             patch.object(law_scraper, "logger") as logger:
            self.assertEqual(await law_scraper.save_to_mongodb_async({"doc_id": "x"}), "")
        logger.error.assert_called_once_with("MongoDB 비동기 저장 실패: x")

    async def test_adrule_passes_keyword_args_and_returns_result(self):
        with patch.object(adrule_scraper, "save_to_mongodb", return_value="update_existing") as save:
            result = await adrule_scraper.save_to_mongodb_async(
                [{"title": "t"}], "제목", "d", "u", "DC", sub_title="s", effective_date="2026-01-01",
                dept_name="부", is_upcoming=True, adrule_id="A", lawgokr_record={"r": 1})
        self.assertEqual(result, "update_existing")
        save.assert_called_once_with([{"title": "t"}], "제목", "d", "u", dept_code="DC", sub_title="s",
                                     effective_date="2026-01-01", dept_name="부", is_upcoming=True,
                                     adrule_id="A", lawgokr_record={"r": 1})

    async def test_adrule_exception_returns_false(self):
        with patch.object(adrule_scraper, "save_to_mongodb", side_effect=RuntimeError("x")), \
             patch.object(adrule_scraper, "logger") as logger:
            self.assertIs(await adrule_scraper.save_to_mongodb_async([], "t", "d", "u"), False)
        logger.error.assert_called_once_with("MongoDB 비동기 저장 실패: x")

    async def test_context_variables_reach_executor_thread(self):
        var = contextvars.ContextVar("probe", default="unset")
        var.set("main")
        seen = []

        def probe(*args, **kwargs):
            seen.append(var.get())
            return "update_existing"

        with ThreadPoolExecutor(max_workers=1) as executor:
            with patch.object(law_scraper, "save_to_mongodb", probe):
                await law_scraper.save_to_mongodb_async({}, executor=executor)
            with patch.object(adrule_scraper, "save_to_mongodb", probe):
                await adrule_scraper.save_to_mongodb_async([], "t", "d", "u", executor=executor)
        self.assertEqual(seen, ["main", "main"])


# ---------------------------------------------------------------- 파일 저장

class SaveToFileTest(unittest.TestCase):
    def test_writes_jsonl_and_creates_directory(self):
        for scraper in (law_scraper, adrule_scraper):
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "sub", "out.jsonl")
                scraper.save_to_file({"a": "가"}, path)
                scraper.save_to_file([{"a": 1}, {"b": 2}], path + "2")
                with open(path, encoding="utf-8") as f:
                    self.assertEqual(f.read(), '{"a": "가"}\n')
                with open(path + "2", encoding="utf-8") as f:
                    self.assertEqual([json.loads(line) for line in f], [{"a": 1}, {"b": 2}])


# ---------------------------------------------------------------- scrape_and_save (가짜 Playwright)

class FakePage:
    def __init__(self, html="<html></html>", locators=None, goto_error=None):
        self.html = html
        self.locators = locators or {}
        self.goto_error = goto_error
        self.goto_calls = []
        self.waits = []
        self.timeouts = []
        self.screenshots = []

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append((url, wait_until, timeout))
        if self.goto_error:
            raise self.goto_error

    async def wait_for_selector(self, selector, state=None, timeout=None):
        self.waits.append((selector, state, timeout))
        raise TimeoutError("no mask")

    async def wait_for_timeout(self, ms):
        self.timeouts.append(ms)

    async def content(self):
        return self.html

    async def screenshot(self, path=None):
        self.screenshots.append(path)

    def locator(self, selector):
        loc = Mock()
        text = self.locators.get(selector, "")
        loc.text_content = AsyncMock(return_value=text)
        loc.inner_html = AsyncMock(return_value=text)
        return loc


class FakePlaywright:
    def __init__(self, page):
        self.page = page
        self.closed = False
        outer = self

        class Browser:
            async def new_page(self_inner):
                return page

            async def close(self_inner):
                outer.closed = True

        class Chromium:
            async def launch(self_inner, headless=True):
                return Browser()

        self.chromium = Chromium()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class LawScrapeAndSaveTest(unittest.IsolatedAsyncioTestCase):
    async def run_scrape(self, page, url=LAW_URL, save_to_db=True, save_jsonl=True, action="new_version", **kwargs):
        playwright = FakePlaywright(page)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(law_scraper, "async_playwright", playwright), \
             patch.object(law_scraper, "project_root", tmp), \
             patch.object(law_scraper, "save_to_mongodb_async", AsyncMock(return_value=action)) as save:
            result = await law_scraper.scrape_and_save(url, os.path.join(tmp, "out"), "name", save_to_db=save_to_db,
                                                       save_jsonl=save_jsonl, **kwargs)
            files = sorted(os.listdir(os.path.join(tmp, "out"))) if os.path.isdir(os.path.join(tmp, "out")) else []
            debug_files = sorted(os.listdir(os.path.join(tmp, "debug"))) if os.path.isdir(os.path.join(tmp, "debug")) else []
            jsonl = None
            if files:
                with open(os.path.join(tmp, "out", files[0]), encoding="utf-8") as f:
                    jsonl = json.loads(f.readline())
        self.assertTrue(playwright.closed)
        return result, save, files, debug_files, jsonl

    async def test_success_with_db(self):
        page = FakePage(LAW_HTML)
        item = {"url": LAW_URL, "effective": "20990101", "dept_name": "고용노동부", "law_id": "LID", "is_upcoming": True}
        result, save, files, _, jsonl = await self.run_scrape(page, url=item, dept_code="1492000")
        self.assertEqual(result, {"doc_id": "100", "status": "success", "action": "new_version"})
        self.assertEqual(page.goto_calls, [(LAW_URL, "domcontentloaded", 60000)])
        self.assertEqual(page.waits, [(".loadmask-msg", "visible", 10000)])
        self.assertEqual(page.timeouts, [1000])
        self.assertEqual(files, ["name_document.jsonl"])
        self.assertEqual((jsonl["dept_name"], jsonl["law_id"], jsonl["is_upcoming"]), ("고용노동부", "LID", True))
        self.assertEqual(jsonl["metadata"]["effective"], "2026-01-05")  # 본문에서 읽은 값이 목록 값보다 우선
        saved_doc, dept_code = save.call_args.args
        self.assertEqual((saved_doc["doc_id"], dept_code), ("100", "1492000"))

    async def test_list_effective_fills_missing_date(self):
        html = LAW_HTML.replace("[시행 2026. 1. 5.] ", "")
        _, save, _, _, jsonl = await self.run_scrape(FakePage(html), url={"url": LAW_URL, "effective": "20990101"})
        self.assertEqual(jsonl["metadata"]["effective"], "2099-01-01")

    async def test_without_db_reports_insert_and_skips_save(self):
        result, save, files, _, _ = await self.run_scrape(FakePage(LAW_HTML), save_to_db=False, save_jsonl=False)
        self.assertEqual(result, {"doc_id": "100", "status": "success", "action": "insert"})
        save.assert_not_called()
        self.assertEqual(files, [])

    async def test_parse_failure(self):
        result, save, files, _, _ = await self.run_scrape(FakePage("<html></html>"))
        self.assertEqual(result, {"doc_id": None, "status": "failed", "error": "문서 파싱 실패"})
        save.assert_not_called()

    async def test_save_failure(self):
        result, *_ = await self.run_scrape(FakePage(LAW_HTML), action="")
        self.assertEqual(result, {"doc_id": "100", "status": "failed", "error": "MongoDB 저장 실패"})

    async def test_exception_dumps_artifacts_and_reports_error(self):
        page = FakePage("<html>err</html>", goto_error=RuntimeError("boom"))
        result, save, files, debug_files, _ = await self.run_scrape(page)
        self.assertEqual(result, {"doc_id": None, "status": "failed", "error": "RuntimeError: boom"})
        self.assertEqual(debug_files, ["name_error.html"])  # 스크린샷은 가짜 페이지라 파일이 없다
        self.assertEqual(len(page.screenshots), 1)
        self.assertTrue(page.screenshots[0].endswith(os.path.join("debug", "name_error.png")))


ADRULE_PAGE_HTML = ('<div class="pgroup"><span class="bl"><label>제1조(목적)</label></span></div>')


def adrule_scrape_page(**kwargs):
    locators = {
        "#conTop h2": " 최저임금  고시 ",
        "div.subtit1": "[시행 2026. 1. 5.] [고용노동부고시 제2025-121호, 2025. 12. 30., 일부개정]",
        "#conScroll": ADRULE_STRUCTURED,
    }
    return FakePage(ADRULE_PAGE_HTML, locators, **kwargs)


class AdruleScrapeAndSaveTest(unittest.IsolatedAsyncioTestCase):
    async def run_scrape(self, page, url="https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=555", action="new_version",
                         record=None, **kwargs):
        playwright = FakePlaywright(page)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(adrule_scraper, "async_playwright", playwright), \
             patch.object(adrule_scraper, "project_root", tmp), \
             patch.object(adrule_scraper, "fetch_adrule_record", AsyncMock(return_value=record)), \
             patch.object(adrule_scraper, "save_to_mongodb_async", AsyncMock(return_value=action)) as save:
            result = await adrule_scraper.scrape_and_save(url, os.path.join(tmp, "out"), "name", **kwargs)
            listing = lambda *p: sorted(os.listdir(os.path.join(tmp, *p))) if os.path.isdir(os.path.join(tmp, *p)) else []
            files, debug_files = listing("out"), listing("debug")
            debug_named = listing("debug", "name")
            read = lambda n: json.loads(open(os.path.join(tmp, "out", n), encoding="utf-8").readline())
            docs = {n: read(n) for n in files}
        self.assertTrue(playwright.closed)
        return result, save, files, debug_files, debug_named, docs

    async def test_success_with_db(self):
        page = adrule_scrape_page()
        record = {"AdmRulService": {"행정규칙기본정보": {"행정규칙ID": "AID"}}}
        item = {"url": "https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=555", "dept_name": "고용노동부", "is_upcoming": True}
        result, save, files, _, _, docs = await self.run_scrape(page, url=item, record=record, dept_code="D")
        self.assertEqual(result, {"status": "success", "doc_id": "555", "title": "최저임금 고시", "chunks_count": 2,
                                  "saved_to_db": True, "action": "new_version"})
        self.assertEqual(page.goto_calls, [(item["url"], "domcontentloaded", 120000)])
        self.assertEqual(page.waits, [(".loadmask-msg", "visible", 1000)])
        self.assertEqual(page.timeouts, [1000])
        self.assertEqual(files, ["name_chunks.jsonl", "name_document.jsonl"])
        self.assertEqual(docs["name_document.jsonl"], {
            "doc_id": "555", "doc_type": "행정예규", "title": "최저임금 고시",
            "sub_title": "고용노동부고시 제2025-121호, 2025. 12. 30., 일부개정", "source_url": item["url"],
            "effective": "2026-01-05", "is_active": True})
        args, kwargs = save.call_args
        self.assertEqual(args[1:5], ("최저임금 고시", "555", item["url"], "D"))
        self.assertEqual(len(args[0]), 2)
        self.assertEqual(kwargs, {"sub_title": "고용노동부고시 제2025-121호, 2025. 12. 30., 일부개정",
                                  "effective_date": "2026-01-05", "dept_name": "고용노동부", "is_upcoming": True,
                                  "adrule_id": "AID", "lawgokr_record": record})

    async def test_doc_id_fallback_from_title(self):
        result, *_ = await self.run_scrape(adrule_scrape_page(), url="https://www.law.go.kr/x")
        self.assertEqual(result["doc_id"], "")  # 한글 제목은 영숫자만 남기면 빈 문자열

    async def test_without_db(self):
        result, save, files, *_ = await self.run_scrape(adrule_scrape_page(), save_to_db=False, save_jsonl=False)
        self.assertEqual(result, {"status": "success", "doc_id": "555", "title": "최저임금 고시", "chunks_count": 2,
                                  "saved_to_db": False, "action": "insert"})
        save.assert_not_called()
        self.assertEqual(files, [])

    async def test_no_chunks_is_failure(self):
        page = adrule_scrape_page()
        page.html = "<div>본문 없음</div>"  # 구조·PDF 뷰어 없음 → 비정형 파서, 본문이 비어 청크 0개
        page.locators["#conScroll"] = ""
        result, save, *_ = await self.run_scrape(page)
        self.assertEqual(result, {"status": "failed", "doc_id": "555", "title": "최저임금 고시", "error": "조문(청크) 없음"})
        save.assert_not_called()

    async def test_save_failure(self):
        result, *_ = await self.run_scrape(adrule_scrape_page(), action="")
        self.assertEqual(result, {"status": "failed", "doc_id": "555", "title": "최저임금 고시", "error": "MongoDB 저장 실패"})

    async def test_debug_writes_page_source(self):
        _, _, _, _, debug_named, _ = await self.run_scrape(adrule_scrape_page(), debug=True)
        self.assertEqual(debug_named, ["name_page_source.html"])

    async def test_exception_dumps_artifacts_and_reports_error(self):
        page = adrule_scrape_page(goto_error=RuntimeError("boom"))
        result, _, _, debug_files, _, _ = await self.run_scrape(page)
        self.assertEqual(result, {"status": "failed", "doc_id": None, "error": "boom"})
        self.assertEqual(debug_files, ["name_error.html"])
        self.assertTrue(page.screenshots[0].endswith(os.path.join("debug", "name_error.png")))


if __name__ == "__main__":
    unittest.main()
