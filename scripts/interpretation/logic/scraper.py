import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup, NavigableString
import json
import re
import os
from urllib.parse import urlparse, parse_qs
import sys
from collections import defaultdict

# --- 프로젝트 루트 경로 설정 ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# --- 로거 설정 ---
from scripts.utils.logger_config import get_logger
logger = get_logger(__name__, scraper_type='case')

def clean_spaces(text: str) -> str:
    """텍스트에서 불필요한 공백과 줄바꿈을 정리합니다."""
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r'(［\d+］)\n\s*', r'\1 ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' +\n', '\n', text)
    text = re.sub(r'(\n\s*){2,}', '\n\n', text)
    return text.strip()

def get_doc_id_from_url(url: str) -> str | None:
    """URL에서 판례 고유 ID (precSeq)를 추출합니다."""
    try:
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        return query_params.get('precSeq', [None])[0]
    except Exception:
        return None

def parse_case_law_content(html: str, doc_id: str, url: str, doc_title: str):
    """판례 상세 페이지의 HTML을 파싱하여 의미 단위의 청크(Chunks)로 분할합니다."""
    soup = BeautifulSoup(html, 'html.parser')

    chunks = []
    current_chunk = None
    section_key_map = {
        "판시사항": "issue", "판결요지": "summary", "참조조문": "references",
        "참조판례": "ref_cases", "연관판결": "related_cases", "인정근거": "basis_of_recognition",
        "원고": "plaintiff", "피고": "defendant",
        "원심판결": "lower_court", "제1심판결": "first_court", "변론종결": "hearing_close",
        "청구취지": "claim_summary", "항소취지": "appeal_summary",
        "청구취지 및 항소취지": "claim_and_appeal_summary", "상고이유": "appeal_reason",
        "주문": "order", "이유": "reasoning", "전문": "full_text"
    }

    # [추가] 중복된 섹션 키의 개수를 세기 위한 딕셔너리
    section_key_counts = defaultdict(int)

    for element in soup.find_all(True, recursive=False):
        element_text = element.get_text(strip=True)
        if not element_text:
            continue

        if element_text.startswith('【'):
            if current_chunk and current_chunk.get('text'):
                current_chunk['text'] = clean_spaces(current_chunk['text'])
                chunks.append(current_chunk)

            section_title = element_text.strip('【】')

            if len(section_title) == 3 and section_title[1] == ' ':
                section_title = section_title.replace(' ', '')

            section_key_raw = section_title.split(',')[0].split('(')[0].strip()
            section_key = section_key_map.get(section_key_raw, "section")

            # [수정] 중복 ID 방지를 위한 로직
            section_key_counts[section_key] += 1
            count = section_key_counts[section_key]

            # section_key가 'section'이거나, count가 1보다 클 때만 숫자를 붙임
            final_section_key = f"{section_key}_{count}" if section_key == "section" or count > 1 else section_key

            current_chunk = {
                "chunk_id": f"doc:{doc_id}:{final_section_key}",
                "doc_id": doc_id, "title": section_title, "text": "",
                "metadata": {"chapter": doc_title}, "source_url": url
            }
        elif current_chunk:
            for br in element.find_all("br"):
                br.replace_with("\n")
            current_chunk["text"] += " " + element.get_text()

    if current_chunk and current_chunk.get('text'):
        current_chunk['text'] = clean_spaces(current_chunk['text'])
        chunks.append(current_chunk)

    # 빈 텍스트를 가진 청크 최종 제거
    chunks = [chunk for chunk in chunks if chunk.get("text")]
    return chunks

async def scrape_and_save(url: str, output_dir: str, output_name: str):
    """웹페이지 컨텐츠를 가져와 document와 chunk로 나누어 파일로 저장합니다."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"페이지로 이동 중: {url}")
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)

            logger.info("제목과 본문 콘텐츠가 로드되기를 기다립니다...")
            await page.locator('#contentBody h2').wait_for(timeout=30000)
            await page.locator('#conScroll').wait_for(timeout=30000)
            logger.info("콘텐츠 로드 완료.")

            doc_title = clean_spaces(await page.locator('#contentBody h2').text_content())
            doc_subtitle = clean_spaces(await page.locator('#subtit1, div.subtit2').first.text_content())
            doc_id = get_doc_id_from_url(url)
            if not doc_id:
                logger.error("URL에서 doc_id(precSeq)를 추출하지 못했습니다.")
                return

            document_data = {
                "doc_id": doc_id, "title": doc_title,
                "subtitle": doc_subtitle, "source_url": url
            }

            content_html = await page.locator('#conScroll').inner_html()
            chunks = parse_case_law_content(content_html, doc_id, url, doc_title)

            doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
            chunk_filename = os.path.join(output_dir, f'{output_name}_chunks.jsonl')

            if document_data.get("title"):
                save_to_file(document_data, doc_filename)
            if chunks:
                save_to_file(chunks, chunk_filename)
            else:
                logger.warning("페이지에서 청크 데이터를 찾지 못했습니다.")
        except Exception as e:
            logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
            debug_dir = os.path.join(project_root, 'debug')
            os.makedirs(debug_dir, exist_ok=True)
            screenshot_path = os.path.join(debug_dir, f'{output_name}_error.png')
            html_path = os.path.join(debug_dir, f'{output_name}_error.html')
            await page.screenshot(path=screenshot_path)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(await page.content())
            logger.info(f"에러 스크린샷: {screenshot_path}, HTML: {html_path}")
        finally:
            await browser.close()

def save_to_file(data, filename):
    """파싱된 데이터를 JSONL 형식으로 파일에 저장합니다."""
    if not isinstance(data, list): data = [data]
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    logger.info(f"데이터 {len(data)}건을 '{filename}' 파일로 성공적으로 저장했습니다.")

