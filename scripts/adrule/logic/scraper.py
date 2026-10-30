import asyncio
from playwright.async_api import Page, async_playwright
from bs4 import BeautifulSoup
import re
import os
from urllib.parse import urlparse, parse_qs
import sys
from datetime import datetime


project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.database.unified_repository import UnifiedDocumentRepository
from scripts.core.identifiers import article_chunk_id, normalize_article_number
from scripts.core.lawgokr_annex import annex_metadata, collect_annex_articles, download_and_extract
from scripts.core.lawgokr_document_saver import (  # noqa: F401 - split_articles_by_number 는 기존 import 경로 유지
    run_in_executor_with_context, save_articles, save_to_file as save_json_lines, split_articles_by_number,
)
from scripts.core.lawgokr_page import dump_error_artifacts, load_rendered_page
from scripts.core.lawgokr_stable_id import fetch_record, stable_adrule_id

logger = get_logger(__name__, scraper_type='adrule')

def clean_spaces(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = s.replace('"', '')
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --- 유형 1 파서 ---
def _parse_structured_content(html: str, doc_id: str, url: str, doc_title: str):
    """'제1조' 등 정형화된 법령 구조를 파싱합니다."""
    soup = BeautifulSoup(html, 'html.parser')
    chunks = []
    current_chapter = ""
    for pgroup_div in soup.select('div.pgroup'):
        chapter_tag = pgroup_div.select_one('p.gtit')
        if chapter_tag: current_chapter = clean_spaces(chapter_tag.get_text())
        title_tag = pgroup_div.select_one('span.bl label')
        if not title_tag or not title_tag.get_text(strip=True): continue
        article_title = title_tag.get_text(strip=True)
        article_id_num = "".join(filter(str.isdigit, article_title.split('(')[0]))
        if not article_id_num: continue

        lawcon_div = pgroup_div.select_one('.lawcon')
        if not lawcon_div: continue

        # 텍스트를 추출하기 전에 모든 a 태그를 제거하여 내용만 남깁니다.
        for a_tag in lawcon_div.find_all('a'):
            a_tag.unwrap()

        # separator 인수를 제거하여 불필요한 줄바꿈을 방지합니다.
        text_content = lawcon_div.get_text()
        final_text = "\n".join(line.strip() for line in text_content.splitlines() if line.strip())

        chunks.append({
            "chunk_id": f"doc:{doc_id}:article_{article_id_num}", "doc_id": doc_id,
            "title": clean_spaces(article_title), "text": clean_spaces(final_text),
            "metadata": {"chapter": current_chapter or doc_title}, "source_url": url
        })
    return chunks

# --- 유형 2 파서 ---
def _parse_unstructured_content(html: str, doc_id: str, url: str, doc_title: str):
    """비정형 텍스트 고시 구조를 파싱합니다."""
    soup = BeautifulSoup(html, 'html.parser') # <-- 수정된 부분: 함수 내부에서 BeautifulSoup 객체 생성
    content_div = soup.select_one('.lawcon') or soup
    chunks = []
    sections, current_section_content = [], []
    for el in content_div.find_all(True, recursive=False):
        el_text = el.get_text(strip=True)
        if not el_text: continue
        if re.match(r'^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s', el_text):
            if current_section_content: sections.append(current_section_content)
            current_section_content = [el]
        else:
            current_section_content.append(el)
    if current_section_content: sections.append(current_section_content)
    chunk_index = 1
    for section_elements in sections:
        first_el_text = section_elements[0].get_text(strip=True)
        is_main_titled = re.match(r'^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s', first_el_text)
        title = first_el_text if is_main_titled else doc_title or "서문"
        text_elements = section_elements[1:] if is_main_titled else section_elements
        text = "\n".join(el.get_text(strip=True) for el in text_elements)
        chunk_id = f"doc:{doc_id}:section_{chunk_index}" if is_main_titled else f"doc:{doc_id}:preamble"
        if is_main_titled: chunk_index += 1
        if not text.strip() and not title.strip() == doc_title: continue
        chunks.append({
            "chunk_id": chunk_id, "doc_id": doc_id,
            "title": clean_spaces(title), "text": clean_spaces(text),
            "metadata": {"chapter": doc_title}, "source_url": url
        })
    return chunks


# --- 유형 3 파서 ---
FILE_DOWNLOAD_URL = "https://www.law.go.kr/LSW/flDownload.do?flSeq={key}"


def viewer_file_keys(html: str) -> list[str]:
    """PDF 뷰어 iframe 주소의 key(= 원본 파일 번호 flSeq). 문서 순서대로, 중복 없이."""
    keys = []
    for frame in BeautifulSoup(html, 'html.parser').select('iframe.fancybox-iframe'):
        match = re.search(r'[?&]key=(\d+)', frame.get('src') or '')
        if match and match.group(1) not in keys:
            keys.append(match.group(1))
    return keys


def _download_and_extract(key: str) -> dict:
    """원본 파일을 받아 chat_generation 으로 파싱한다(표는 Markdown 표로 남는다). 실패는 예외로 올린다."""
    return download_and_extract(FILE_DOWNLOAD_URL.format(key=key), fallback_name=f"{key}.pdf")


async def _parse_pdf_viewer_content(page, doc_id: str, url: str, doc_title: str, output_name: str, debug: bool = False):
    """PDF 뷰어(iframe)로만 본문을 보여 주는 문서(최저임금 고시 등).

    예전에는 뷰어 화면을 캡처해 이미지 OCR 을 했고, OCR 이 실패하면 오류 문구가 본문으로 저장됐다.
    이제 뷰어가 가리키는 원본 파일을 내려받아 다른 첨부와 같은 chat_generation 파서로 읽는다.
    하나라도 못 읽으면 예외를 올려 문서 실패로 보고된다(빈 청크 → scrape_and_save 가 failed).
    """
    keys = viewer_file_keys(await page.content())
    if not keys:
        raise ValueError("PDF 뷰어에서 원본 파일 번호(key)를 찾지 못함")

    chunks = []
    for index, key in enumerate(keys, 1):
        extracted = await asyncio.to_thread(_download_and_extract, key)
        text = extracted["structured_text"].strip()
        if debug:
            debug_dir = os.path.join(project_root, 'debug', output_name)
            os.makedirs(debug_dir, exist_ok=True)
            with open(os.path.join(debug_dir, f'{output_name}_file_{key}.md'), 'w', encoding='utf-8') as f:
                f.write(text)
        chunks.append({
            "chunk_id": f"doc:{doc_id}:file_{index}", "doc_id": doc_id,
            # 한 파일이면 제목 줄을 붙이지 않는다(파서 결과에 이미 제목이 있다). 여러 파일이면 구분용 번호만.
            "title": "" if len(keys) == 1 else f"첨부 {index}",
            "text": text,
            "metadata": {
                "chapter": doc_title, "source_type": "pdf", "source_file_key": key,
                "pdf_extraction_status": extracted.get("extraction_status"),
            },
            "source_url": url,
        })
    return chunks

async def parse_law_html(page: 'Page', doc_id: str, doc_title: str, url: str, output_name: str, debug: bool = False):
    """페이지 객체를 받아 유형을 판별하고 적절한 파서를 호출하는 컨트롤 타워."""
    outer_html = await page.content()
    soup = BeautifulSoup(outer_html, 'html.parser')

    is_structured = any(
        label.get_text(strip=True).startswith('제')
        for label in soup.select('div.pgroup span.bl label')
    )
    contains_pdf_iframe = bool(soup.select_one('iframe.fancybox-iframe'))

    content_selector = '#conScroll'

    if is_structured:
        logger.info("유형 1: 정형화된 법령 구조 감지")
        content_html = await page.locator(content_selector).inner_html()
        return _parse_structured_content(content_html, doc_id, url, doc_title)
    elif contains_pdf_iframe:
        logger.info("유형 3: PDF 뷰어(iframe) 구조 감지 (원본 파일 파싱)")
        return await _parse_pdf_viewer_content(page, doc_id, url, doc_title, output_name, debug)
    else:
        logger.info("유형 2: 텍스트 기반 비정형 구조 감지")
        content_html = await page.locator(content_selector).inner_html()
        return _parse_unstructured_content(content_html, doc_id, url, doc_title)

def save_to_file(data, filename):
    save_json_lines(data, filename, logger)


def save_to_mongodb(chunks: list, doc_title: str, doc_id: str, url: str, dept_code: str = None,
                   sub_title: str = "", effective_date: str = None, dept_name: str = None,
                   is_upcoming: bool = False, adrule_id: str = None, lawgokr_record: dict = None) -> str:
    """
    행정예규를 MongoDB에 조별로 분리하여 저장 (law.py와 동일)
    
    처리 흐름:
    1. 모든 청크를 하나의 content로 통합
    2. content를 정규표현식으로 조별 분리
    3. 각 조마다 독립 문서 생성 (doc_id + 조번호로 구분)
    4. 각 조를 MongoDB에 저장
    
    Args:
        chunks: 파싱된 청크 리스트
        doc_title: 문서 제목
        doc_id: 문서 ID
        url: 원본 URL
        dept_code: 부처 코드 (옵션)
        sub_title: 부제 (시행일자 및 고시 정보)
        effective_date: 효력일자 (YYYY-MM-DD 형식)
    
    Returns:
        저장 성공 여부
    """
    try:
        if not chunks:
            logger.warning("저장할 청크가 없습니다.")
            return False
        
        if effective_date is None:
            effective_date = ""
        
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        repo = UnifiedDocumentRepository(db, collection_name="adrule")
        
        # 모든 청크를 하나의 content로 통합
        content_parts = []
        for chunk in chunks:
            if isinstance(chunk, dict):
                title = chunk.get("title", "")
                text = chunk.get("text", "")
                chunk_text = f"[{title}]\n{text}" if title else text
            else:
                chunk_text = str(chunk)
            
            if chunk_text.strip():
                content_parts.append(chunk_text)
        
        unified_content = "\n\n".join(content_parts) if content_parts else ""

        # 원본 파일(PDF 뷰어)에서 읽은 문서는 출처·추출 상태를 남긴다. partial 이면 일부 페이지를 못 읽은 것이라
        # 나중에 파서가 좋아졌을 때 다시 파싱할 대상을 찾을 수 있게 한다.
        source_file_meta = {}
        file_chunks = [c for c in chunks if isinstance(c, dict) and (c.get("metadata") or {}).get("source_file_key")]
        if file_chunks:
            statuses = [c["metadata"].get("pdf_extraction_status") for c in file_chunks]
            source_file_meta = {
                "source_type": "pdf",
                "source_file_keys": [c["metadata"]["source_file_key"] for c in file_chunks],
                "pdf_extraction_status": "complete" if all(st == "complete" for st in statuses) else "partial",
            }
            if source_file_meta["pdf_extraction_status"] != "complete":
                logger.warning(f"⚠️ {doc_id}: 원본 파일 일부 페이지를 읽지 못함(partial) — 읽은 부분만 저장")
        
        # 조별 분리
        articles = split_articles_by_number(unified_content)
        
        if not articles:
            logger.warning("분리된 조문이 없습니다. 통합 문서 그대로 저장합니다.")
            articles = [{
                "article_number": None,
                "article_number_numeric": None,
                "article_title": None,
                "content": unified_content
            }]

        # 별표 첨부(표가 대부분)를 원본 파일로 읽어 함께 저장한다. 못 읽으면 문서는 실패로 센다.
        if lawgokr_record is None:
            lawgokr_record = fetch_record("admrul", doc_id)
        annex_rows, annex_failed = collect_annex_articles("adrule", lawgokr_record, logger)
        articles = articles + annex_rows
        
        # 각 조마다 독립 문서 생성 및 저장 (공통 루프: lawgokr_document_saver.save_articles)
        def build_doc(idx, article, total):
            # 조별 문서 생성 (law.py와 동일한 스키마)
            article_number = normalize_article_number(
                article['article_number'],
                fallback=str(idx),
            )

            meta = {
                "source_url": url,
                "source_type": "web",
                "effective": effective_date,
                "created_at": datetime.now().strftime('%Y-%m-%d'),
                "article_index": idx,
                "total_articles": total,
                "is_upcoming": is_upcoming,
            }
            if dept_code:
                meta["dept_code"] = dept_code
            if dept_name:
                meta["dept_name"] = dept_name
            if adrule_id:
                meta["adrule_id"] = adrule_id
            meta.update(source_file_meta)
            meta.update(annex_metadata(article))

            return {
                "chunk_id": article_chunk_id("adrule", doc_id, article_number),
                "doc_id": doc_id,
                "article_number": article_number,
                "article_title": article['article_title'],
                "title": doc_title,
                "sub_title": sub_title,
                "content": article['content'],
                "doc_type": "행정규칙",
                "metadata": meta,
            }

        def log_action(_, action_result, article_doc):
            article_number = article_doc["article_number"]
            if action_result["action"] == "insert":
                logger.info(f"💾 신규 저장: {doc_id} (article_number: {article_number})")
            elif action_result["action"] == "new_version":
                logger.info(f"✅ 새 버전 생성: {doc_id} (article_number: {article_number})")
            elif action_result["action"] == "update_existing":
                logger.info(f"🔄 기존 버전 유지: {doc_id} (article_number: {article_number})")

        # 변경 감지 포함 upsert (law.py와 동일한 버전관리 경로)
        return save_articles(repo, articles, build_doc=build_doc, log_action=log_action,
                             source_url=url, annex_failed=annex_failed, logger=logger)

    except Exception as e:
        logger.error(f"MongoDB 저장 중 오류 발생: {str(e)}")
        return False


async def fetch_adrule_record(doc_id: str) -> dict | None:
    """Open API 조회(동기 요청 + 재시도 대기 5·10초)가 이벤트 루프를 막지 않도록 스레드에서 돌린다."""
    return await asyncio.to_thread(fetch_record, "admrul", doc_id)


async def save_to_mongodb_async(chunks: list, doc_title: str, doc_id: str, url: str, dept_code: str = None,
                                sub_title: str = "", effective_date: str = None, executor=None,
                                dept_name: str = None, is_upcoming: bool = False,
                                adrule_id: str = None, lawgokr_record: dict = None) -> str:
    """MongoDB 저장을 async로 처리 (event loop blocking 방지)"""
    try:
        return await run_in_executor_with_context(
            executor, save_to_mongodb,
            chunks, doc_title, doc_id, url,
            dept_code=dept_code, sub_title=sub_title, effective_date=effective_date,
            dept_name=dept_name, is_upcoming=is_upcoming, adrule_id=adrule_id,
            lawgokr_record=lawgokr_record,
        )
    except Exception as e:
        logger.error(f"MongoDB 비동기 저장 실패: {e}")
        return False

async def scrape_and_save(url: str | dict, output_dir: str, output_name: str, debug: bool = False, save_to_db: bool = True, save_jsonl: bool = True, dept_code: str = None):
    """Playwright를 실행하여 웹페이지 컨텐츠를 가져오고 파일로 저장합니다."""
    dept_name = None
    is_upcoming = False
    if isinstance(url, dict):
        dept_name = url.get("dept_name", "")
        is_upcoming = bool(url.get("is_upcoming", False))
        url = url.get("url", "")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            # 최종 렌더링 HTML 은 아래에서 필요한 부분을 page 에서 직접 읽는다(반환값은 쓰지 않는다).
            await load_rendered_page(page, url, logger, goto_timeout=120000,
                                     mask_visible_timeout=1000, mask_hidden_timeout=3000)
            logger.info("데이터 파싱 중...")

            # ▼▼▼ 디버그 코드 추가 ▼▼▼
            if debug:
                debug_dir = os.path.join(project_root, 'debug', output_name)
                if not os.path.exists(debug_dir):
                    os.makedirs(debug_dir)

                # 현재 페이지의 전체 HTML 저장
                html_path = os.path.join(debug_dir, f'{output_name}_page_source.html')
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(await page.content())
                logger.info(f"디버그용 HTML 소스 저장됨: {html_path}")
                # ▲▲▲ 디버그 코드 추가 ▲▲▲

            doc_title = clean_spaces(await page.locator('#conTop h2').text_content())
            doc_subtitle = clean_spaces(await page.locator('div.subtit1').text_content())

            # ■■■ 효력일자 추출 (law.py와 동일 로직) ■■■
            effective_date = ""  # 추출 실패 시 빈값 유지 (datetime.now 쓰면 매일 false-positive 발생)
            try:
                # doc_subtitle에서 먼저 시행일자 추출 시도
                # [시행 2026. 1. 1.] 패턴 매칭
                if doc_subtitle:
                    match = re.search(r'\[시행\s+(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})[.\]]*', doc_subtitle)
                    if match:
                        effective_date = f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
                        logger.info(f"효력일자 추출: {effective_date}")
            except Exception as e:
                logger.warning(f"효력일자 추출 중 오류: {e}")

            # ■■■ sub_title 정제 (법령명 + 고시번호, 시행일자 제거) ■■■
            sub_title = ""
            if doc_subtitle:
                sub_title = doc_subtitle
                # 대괄호 제거: [고용노동부고시 제2025-121호, 2025. 12. 30., 일부개정] → 고용노동부고시 제2025-121호, 2025. 12. 30., 일부개정
                sub_title = re.sub(r'\[([^\[\]]+)\]', r'\1', sub_title)
                # "시행 YYYY. MM. DD." 패턴 제거
                sub_title = re.sub(r'시행\s+\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}[.]?\s*', '', sub_title)
                # 여러 공백을 하나로, 앞뒤 공백 제거
                sub_title = re.sub(r'\s+', ' ', sub_title).strip()
                logger.info(f"정제된 sub_title: {sub_title}")

            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            doc_id_keys = ['admRulSeq', 'admRulId']
            doc_id = next((query_params.get(key, [None])[0] for key in doc_id_keys if query_params.get(key)), None)
            if not doc_id:
                doc_id = re.sub(r'[^a-zA-Z0-9]', '', doc_title)

            # 행정규칙ID(안정 ID)·별표 첨부를 Open API 한 번으로 조회 — 안정 ID 는 실패해도 계속 진행
            lawgokr_record = await fetch_adrule_record(doc_id)
            adrule_id = stable_adrule_id(lawgokr_record)
            if not adrule_id:
                logger.warning(f"행정규칙ID API 조회 실패: doc_id={doc_id}")

            document_data = {
                "doc_id": doc_id, 
                "doc_type": "행정예규",
                "title": doc_title, 
                "sub_title": sub_title, 
                "source_url": url,
                "effective": effective_date,
                "is_active": True
            }

            chunks = await parse_law_html(page, doc_id, doc_title, url, output_name, debug)

            # JSONL 파일로 저장 (save_jsonl=True인 경우에만)
            if save_jsonl:
                doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
                chunk_filename = os.path.join(output_dir, f'{output_name}_chunks.jsonl')

                if document_data.get("title"):
                    save_to_file(document_data, doc_filename)
                if chunks:
                    save_to_file(chunks, chunk_filename)
                else:
                    logger.warning("페이지에서 청크(조문) 데이터를 찾지 못했습니다.")
            
            # MongoDB에 저장 (save_to_db=True인 경우에만)
            # sub_title과 effective_date를 전달하여 메타데이터 완전성 보장
            if save_to_db and chunks:
                _adrule_action = await save_to_mongodb_async(
                    chunks,
                    doc_title,
                    doc_id,
                    url,
                    dept_code,
                    sub_title=sub_title,
                    effective_date=effective_date,
                    dept_name=dept_name,
                    is_upcoming=is_upcoming,
                    adrule_id=adrule_id,
                    lawgokr_record=lawgokr_record,
                )
            
            if save_to_db and not chunks:
                # 조문을 하나도 못 읽은 문서는 저장된 것이 없다 → 성공으로 세지 않는다.
                return {"status": "failed", "doc_id": doc_id, "title": doc_title, "error": "조문(청크) 없음"}
            if save_to_db and not _adrule_action:
                return {"status": "failed", "doc_id": doc_id, "title": doc_title, "error": "MongoDB 저장 실패"}

            # 성공적으로 완료된 문서 정보 반환
            return {
                "status": "success",
                "doc_id": doc_id,
                "title": doc_title,
                "chunks_count": len(chunks) if chunks else 0,
                "saved_to_db": save_to_db and bool(chunks),
                "action": _adrule_action if (save_to_db and chunks) else "insert",
            }

        except Exception as e:
            logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
            screenshot_path, html_path = await dump_error_artifacts(page, output_name, project_root)

            logger.info(f"에러 스크린샷: {screenshot_path}, HTML: {html_path}")
            
            # 에러 반환
            return {
                "status": "failed",
                "doc_id": None,
                "error": str(e),
            }

        finally:
            await browser.close()
