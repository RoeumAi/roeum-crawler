import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import json
import re
import os
from urllib.parse import urlparse, parse_qs, urljoin
import sys
import requests
from PIL import Image
import pytesseract
import io

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='adrule')

def clean_spaces(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = s.replace('"', '')
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _image_to_text(image_url: str) -> str:
    """이미지 URL에서 텍스트를 추출합니다 (OCR)."""
    if not image_url: return ""
    try:
        logger.info(f"이미지 OCR 시작: {image_url}")
        response = requests.get(image_url, stream=True)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        text = pytesseract.image_to_string(image, lang='kor')
        logger.info("이미지 OCR 성공")
        return f"\n[이미지 내용 시작]\n{text.strip()}\n[이미지 내용 끝]\n"
    except Exception as e:
        logger.error(f"이미지 OCR 처리 중 오류 발생: {e}")
        return f"\n[이미지 처리 오류: {image_url}]\n"


def _image_bytes_to_text(image_bytes: bytes) -> str:
    """메모리에 있는 이미지 바이트에서 텍스트를 추출합니다 (OCR)."""
    if not image_bytes: return ""
    try:
        logger.info("메모리 내 이미지 OCR 시작...")
        image = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(image, lang='kor')
        logger.info("메모리 내 이미지 OCR 시작...")
        image = Image.open(io.BytesIO(image_bytes))
        config = '--psm 6'
        text = pytesseract.image_to_string(image, lang='kor', config=config)
        logger.info("메모리 내 이미지 OCR 성공")
        text = re.sub(r'\n{2,}', '\n', text)
        text = text.replace('\n', ' ')
        return text.strip()
    except Exception as e:
        logger.error(f"이미지 바이트 OCR 처리 중 오류 발생: {e}")
        return "\n[이미지 처리 오류]\n"

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
async def _parse_pdf_viewer_content(page, doc_id: str, url: str, doc_title: str, output_name: str):
    """PDF 뷰어(iframe)가 포함된 구조를 파싱합니다 (OCR 사용)."""
    chunks = []
    try:
        iframe = page.frame_locator('iframe.fancybox-iframe')
        image_locators = iframe.locator('div#contents-area img')
        image_count = await image_locators.count()
        logger.info(f"{image_count}개의 PDF 페이지 이미지 발견")

        content_parts = []
        for i in range(image_count):
            image_locator = image_locators.nth(i)
            await image_locator.scroll_into_view_if_needed()
            await page.wait_for_timeout(200)

            image_bytes = await image_locator.screenshot()

            # --- 디버깅: 스크린샷 파일로 저장 ---
            debug_dir = os.path.join(project_root, 'debug')
            if not os.path.exists(debug_dir):
                os.makedirs(debug_dir)
            screenshot_path = os.path.join(debug_dir, f'{output_name}_ocr_image_{i+1}.png')
            with open(screenshot_path, 'wb') as f:
                f.write(image_bytes)
            logger.info(f"OCR용 스크린샷 저장됨: {screenshot_path}")
            # --- 디버깅 코드 끝 ---

            content_parts.append(_image_bytes_to_text(image_bytes))

        full_text = "\n".join(part for part in content_parts if part and part.strip())

        if full_text:
            chunks.append({
                "chunk_id": f"doc:{doc_id}:full_content_ocr", "doc_id": doc_id,
                "title": doc_title, "text": clean_spaces(full_text),
                "metadata": {"chapter": doc_title, "source_type": "pdf_ocr"}, "source_url": url
            })
    except Exception as e:
        logger.error(f"PDF 뷰어 처리 중 오류 발생: {e}")
    return chunks

async def parse_law_html(page: 'Page', doc_id: str, doc_title: str, url: str, output_name: str):
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
        logger.info("유형 3: PDF 뷰어(iframe) 구조 감지 (OCR 실행)")
        return await _parse_pdf_viewer_content(page, doc_id, url, doc_title, output_name)
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

async def scrape_and_save(url: str, output_dir: str, output_name: str):
    """Playwright를 실행하여 웹페이지 컨텐츠를 가져오고 파일로 저장합니다."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"페이지로 이동 중: {url}")
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)

            try:
                logger.info("로딩 마스크가 나타나기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="visible", timeout=10000)
                logger.info("로딩 마스크가 사라지기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=30000)
            except Exception:
                logger.warning("로딩 마스크가 감지되지 않았습니다. 페이지 로드가 완료된 것으로 간주하고 계속 진행합니다.")

            logger.info("페이지 최종 렌더링을 잠시 대기합니다...")
            await page.wait_for_timeout(1000)
            logger.info("페이지 HTML 컨텐츠를 가져오는 중...")
            html = await page.content()
            logger.info("데이터 파싱 중...")

            doc_title = clean_spaces(await page.locator('#conTop h2').text_content())
            doc_subtitle = clean_spaces(await page.locator('div.ct_sub, div.subtit1').text_content())

            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            doc_id_keys = ['admRulSeq', 'admRulId']
            doc_id = next((query_params.get(key, [None])[0] for key in doc_id_keys if query_params.get(key)), None)
            if not doc_id:
                doc_id = re.sub(r'[^a-zA-Z0-9]', '', doc_title)

            document_data = { "doc_id": doc_id, "title": doc_title, "subtitle": doc_subtitle, "source_url": url }

            chunks = await parse_law_html(page, doc_id, doc_title, url, output_name)

            doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
            chunk_filename = os.path.join(output_dir, f'{output_name}_chunks.jsonl')

            if document_data.get("title"):
                save_to_file(document_data, doc_filename)
            if chunks:
                save_to_file(chunks, chunk_filename)
            else:
                logger.warning("페이지에서 청크(조문) 데이터를 찾지 못했습니다.")


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

        finally:
            await browser.close()

# 3. if __name__ == "__main__": 블록 전체 삭제