import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import json
import re
import os
from urllib.parse import urlparse, parse_qs, urljoin
import sys
import requests
from datetime import datetime


project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.utils.document_extraction import call_extract_text, usable_document
from scripts.core.database.unified_repository import UnifiedDocumentRepository
from scripts.core.identifiers import article_chunk_id, normalize_article_number
from scripts.core.lawgokr_stable_id import fetch_stable_adrule_id

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
    response = requests.get(FILE_DOWNLOAD_URL.format(key=key), headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    response.raise_for_status()
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r'filename="?([^";]+)', disposition)
    file_name = requests.utils.unquote(match.group(1)) if match else f"{key}.pdf"
    return usable_document(call_extract_text(response.content, file_name))


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
    if not isinstance(data, list): data = [data]
    directory = os.path.dirname(filename)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
        logger.info(f"'{directory}' 폴더를 생성했습니다.")
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    logger.info(f"데이터 {len(data)}건을 '{filename}' 파일로 성공적으로 저장했습니다.")


def split_articles_by_number(content: str) -> list:
    """
    통합 content를 조(Article)별로 분리 (law.py와 동일 로직)
    
    예: "[제1조(정의)]\n내용..." → [{"article_number": "1", "article_number_numeric": 1.0, "article_title": "제1조(정의)", "content": "내용..."}]
    
    Args:
        content: 통합 content 텍스트
    
    Returns:
        조별로 분리된 딕셔너리 리스트
    """
    articles = []
    
    # 정규표현식: [제X조(...)]\n내용... 패턴 매칭
    pattern = r'\[제(\d+)(?:조(?:의(\d+))?)\(([^\)]+)\)\]\n((?:(?!\[제\d+).)*)'
    
    matches = re.finditer(pattern, content, re.DOTALL)
    
    for match in matches:
        article_num = match.group(1)
        sub_num = match.group(2)
        article_topic = match.group(3)
        article_content = match.group(4).strip()
        
        # article_number 포맷: "1", "5", "5.1" 등
        if sub_num:
            article_number = f"{article_num}.{sub_num}"
            article_number_numeric = float(f"{article_num}.{sub_num}")
        else:
            article_number = article_num
            article_number_numeric = float(article_num)
        
        # article_title 포맷: "제1조(정의)", "제5조의1(...)" 등
        if sub_num:
            article_title = f"제{article_num}조의{sub_num}({article_topic})"
        else:
            article_title = f"제{article_num}조({article_topic})"
        
        articles.append({
            "article_number": article_number,
            "article_number_numeric": article_number_numeric,
            "article_title": article_title,
            "content": article_content
        })
    
    return articles


def save_to_mongodb(chunks: list, doc_title: str, doc_id: str, url: str, dept_code: str = None,
                   sub_title: str = "", effective_date: str = None, dept_name: str = None,
                   is_upcoming: bool = False, adrule_id: str = None) -> str:
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
        
        # 각 조마다 독립 문서 생성 및 저장
        saved_count = 0
        failed_count = 0
        any_changed = False
        
        for idx, article in enumerate(articles, 1):
            try:
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
                    "total_articles": len(articles),
                    "is_upcoming": is_upcoming,
                }
                if dept_code:
                    meta["dept_code"] = dept_code
                if dept_name:
                    meta["dept_name"] = dept_name
                if adrule_id:
                    meta["adrule_id"] = adrule_id
                meta.update(source_file_meta)

                article_doc = {
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

                # 변경 감지 포함 upsert (law.py와 동일한 버전관리 경로)
                _, action_result = repo.upsert_with_change_detection(article_doc)

                if action_result["action"] == "insert":
                    logger.info(f"💾 신규 저장: {doc_id} (article_number: {article_number})")
                elif action_result["action"] == "new_version":
                    logger.info(f"✅ 새 버전 생성: {doc_id} (article_number: {article_number})")
                elif action_result["action"] == "update_existing":
                    logger.info(f"🔄 기존 버전 유지: {doc_id} (article_number: {article_number})")

                if action_result["action"] in ("insert", "new_version"):
                    any_changed = True
                saved_count += 1

            except Exception as e:
                logger.error(f"❌ 조문 저장 실패 ({article['article_title']}): {str(e)}")
                failed_count += 1
                continue
        
        logger.info(f"📊 MongoDB 저장 완료: 성공 {saved_count}개, 실패 {failed_count}개")
        try:
            repo.mark_save_complete(url, complete=failed_count == 0)
        except Exception as e:  # noqa: BLE001 - 표시 실패는 저장 결과로 판단한다
            logger.error(f"저장 완료 표시 실패: {e}")
            failed_count += 1
        if failed_count > 0:
            # 조문 하나라도 못 넣으면 실패로 돌려준다. save_incomplete 표시로 다음 실행이 다시 수집한다.
            return ""
        return "new_version" if any_changed else "update_existing"
            
    except Exception as e:
        logger.error(f"MongoDB 저장 중 오류 발생: {str(e)}")
        return False


async def save_to_mongodb_async(chunks: list, doc_title: str, doc_id: str, url: str, dept_code: str = None,
                                sub_title: str = "", effective_date: str = None, executor=None,
                                dept_name: str = None, is_upcoming: bool = False,
                                adrule_id: str = None) -> str:
    """MongoDB 저장을 async로 처리 (event loop blocking 방지)"""
    import functools
    loop = asyncio.get_running_loop()
    try:
        fn = functools.partial(
            save_to_mongodb,
            chunks, doc_title, doc_id, url,
            dept_code=dept_code, sub_title=sub_title, effective_date=effective_date,
            dept_name=dept_name, is_upcoming=is_upcoming, adrule_id=adrule_id,
        )
        result = await loop.run_in_executor(executor, fn)
        return result
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
            logger.info(f"페이지로 이동 중: {url}")
            await page.goto(url, wait_until='domcontentloaded', timeout=120000)

            try:
                logger.info("로딩 마스크가 나타나기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="visible", timeout=1000)
                logger.info("로딩 마스크가 사라지기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=3000)
            except Exception:
                logger.warning("로딩 마스크가 감지되지 않았습니다. 페이지 로드가 완료된 것으로 간주하고 계속 진행합니다.")

            logger.info("페이지 최종 렌더링을 잠시 대기합니다...")
            await page.wait_for_timeout(1000)
            logger.info("페이지 HTML 컨텐츠를 가져오는 중...")
            html = await page.content()
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

            # 행정규칙ID(안정 ID) 조회 — 실패해도 크롤링은 계속 진행
            adrule_id = fetch_stable_adrule_id(doc_id)
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
            error_dir = os.path.join(project_root, 'debug')
            if not os.path.exists(error_dir):
                os.makedirs(error_dir)

            screenshot_path = os.path.join(error_dir, f'{output_name}_error.png')
            html_path = os.path.join(error_dir, f'{output_name}_error.html')

            await page.screenshot(path=screenshot_path)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(await page.content())

            logger.info(f"에러 스크린샷: {screenshot_path}, HTML: {html_path}")
            
            # 에러 반환
            return {
                "status": "failed",
                "doc_id": None,
                "error": str(e),
            }

        finally:
            await browser.close()
