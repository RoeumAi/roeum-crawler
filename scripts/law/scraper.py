import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import json
import re
import os
from urllib.parse import urlparse, parse_qs
import sys

project_root = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
sys.path.append(project_root)
from utils.logger_config import get_logger

logger = get_logger(__name__)

def clean_spaces(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def parse_law_html(html: str, url: str):
    soup = BeautifulSoup(html, 'html.parser')
    doc_title_tag = soup.select_one('#conTop h2')
    doc_title = doc_title_tag.get_text(strip=True) if doc_title_tag else ""
    doc_subtitle_tag = soup.select_one('div.ct_sub')
    doc_subtitle = doc_subtitle_tag.get_text(strip=True) if doc_subtitle_tag else ""
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    doc_id_keys = ['lsiSeq', 'lsId']
    doc_id = next((query_params.get(key, [None])[0] for key in doc_id_keys if query_params.get(key)), None)
    if not doc_id:
        logger.warning(f"URL에서 문서 ID(lsiSeq, lsId)를 찾을 수 없습니다. 제목 기반으로 ID를 생성합니다. URL: {url}")
        doc_id = re.sub(r'[^a-zA-Z0-9]', '', doc_title)
    document_data = { "doc_id": doc_id, "title": doc_title, "subtitle": doc_subtitle, "source_url": url }
    content_div = soup.select_one('#contentBody, #conScroll, .law-view-content')
    if not content_div:
        logger.error("법률 내용이 담긴 메인 컨테이너를 찾지 못했습니다.")
        return document_data, []
    chunks = []
    current_chapter = ""
    for pgroup_div in content_div.select('div.pgroup'):
        chapter_tag = pgroup_div.select_one('p.gtit')
        if chapter_tag:
            chapter_text = chapter_tag.get_text(strip=True)
            if re.match(r'^제\s*\d+\s*장', chapter_text):
                current_chapter = chapter_text
        title_tag = pgroup_div.select_one('span.bl label')
        if not title_tag: continue
        article_title = title_tag.get_text(strip=True)
        article_id_num = "".join(filter(str.isdigit, article_title.split('(')[0]))
        if not article_id_num: continue
        for a_tag in pgroup_div.find_all('a'):
            a_tag.unwrap()
        temp_soup = BeautifulSoup(str(pgroup_div), 'html.parser')
        for tag in temp_soup.find_all(['p', 'br']):
            tag.append('\n')
        text_with_newlines = temp_soup.get_text(separator="")
        final_text = "\n".join(line.strip() for line in text_with_newlines.splitlines() if line.strip())
        chunk_data = {
            "chunk_id": f"doc:{doc_id}:article_{article_id_num}", "doc_id": doc_id,
            "title": article_title, "text": final_text,
            "metadata": {"chapter": current_chapter}, "source_url": url
        }
        chunks.append(chunk_data)
    return document_data, chunks

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
    # ... (기존 main 함수의 모든 내용이 여기에 위치)
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
            doc_data, chunk_data = parse_law_html(html, url)

            doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
            chunk_filename = os.path.join(output_dir, f'{output_name}_chunks.jsonl')
            if doc_data.get("title"):
                save_to_file(doc_data, doc_filename)
            else:
                logger.warning("문서 제목을 찾지 못해 document 파일을 저장하지 않습니다.")
            if chunk_data:
                save_to_file(chunk_data, chunk_filename)
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
            logger.info(f"에러 발생 시점의 스크린샷: {screenshot_path}")
            logger.info(f"에러 발생 시점의 HTML: {html_path}")
        finally:
            await browser.close()

# 3. if __name__ == "__main__": 블록 전체 삭제