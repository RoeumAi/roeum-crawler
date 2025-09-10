import asyncio
from playwright.async_api import async_playwright, expect
import json
import re
import os
import sys
import math
from datetime import datetime
import random

# 프로젝트 루트 경로 설정
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

# 스크레이퍼 타입에 맞는 로거 생성
logger = get_logger(__name__, scraper_type='interpretation')

def parse_subtitle(subtitle_text):
    """부제목 텍스트에서 부서, 문서번호, 날짜를 추출합니다."""
    logger.debug(f"Parsing subtitle: {subtitle_text}")
    # 날짜 형식을 더 유연하게 처리 (공백 및 한 자리 숫자 포함)
    date_pattern = r'(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})\.'

    # 패턴 1: [부서 문서번호, 날짜]
    match = re.search(r'\[\s*(.*?)\s+(.*?),\s*' + date_pattern + r'\s*\]', subtitle_text)
    if match:
        department = match.group(1).strip()
        doc_number = match.group(2).strip()
        doc_date = match.group(3).strip().replace(" ", "")
        logger.debug(f"Parsed as Pattern 1: Dept='{department}', Num='{doc_number}', Date='{doc_date}'")
        return department, doc_number, doc_date

    # 패턴 2: [부서, 날짜]
    match = re.search(r'\[\s*(.*?),\s*' + date_pattern + r'\s*\]', subtitle_text)
    if match:
        department = match.group(1).strip()
        doc_date = match.group(2).strip().replace(" ", "")
        logger.debug(f"Parsed as Pattern 2: Dept='{department}', Num='정보 없음', Date='{doc_date}'")
        return department, "정보 없음", doc_date

    logger.warning(f"Subtitle parsing failed for: {subtitle_text}")
    return "정보 없음", "정보 없음", "정보 없음"

async def scrape_interpretation_data(start_url: str, dept_code: str, output_dir: str, max_pages_arg: int | None):
    """
    필터링, 목록 순회, RAG용 상세 정보 추출까지 모두 수행하여 개별 파일로 저장합니다.
    """
    logger.info("Scraping process started.")
    os.makedirs(output_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        current_processing_item = "N/A"
        try:
            logger.info(f"Navigating to start page: {start_url}")
            await page.goto(start_url, wait_until='networkidle', timeout=60000)
            logger.info("Page navigation successful.")

            total_items_locator = page.locator("#writeNumDiv > strong")
            initial_total_items_text = await total_items_locator.inner_text(timeout=10000)

            logger.info("Opening detailed search panel...")
            await page.evaluate("dtlSchOpen('11')")

            logger.info(f"Selecting department code: {dept_code}")
            await page.wait_for_selector('select#selectDtlCgmExpc', state='visible', timeout=10000)
            await page.select_option('select#selectDtlCgmExpc', value=dept_code)

            logger.info("Applying filter by clicking search button...")
            await page.evaluate("newDtlEtcSearch('cgmExpcItmNm')")

            logger.info("Waiting for page to update after filtering...")
            await expect(total_items_locator).not_to_have_text(initial_total_items_text, timeout=30000)
            await page.wait_for_load_state('networkidle', timeout=30000)
            logger.info("Filter application complete.")

            total_pages = 1
            try:
                total_items_text = await total_items_locator.inner_text()
                total_items = int(re.sub(r'[^0-9]', '', total_items_text))
                total_pages = math.ceil(total_items / 50)
                logger.info(f"Found {total_items} items across {total_pages} pages.")
            except Exception as e:
                logger.error(f"Failed to calculate total pages: {e}", exc_info=True)
                return

            pages_to_crawl = total_pages
            if max_pages_arg is not None and 0 < max_pages_arg < total_pages:
                pages_to_crawl = max_pages_arg
                logger.info(f"User override: Crawling a maximum of {pages_to_crawl} pages.")

            for page_num in range(1, pages_to_crawl + 1):
                logger.info(f"--- Starting page {page_num} / {pages_to_crawl} ---")
                if page_num > 1:
                    logger.info(f"Navigating to page {page_num}...")
                    delay = random.uniform(1.5, 3.0)
                    logger.debug(f"Waiting for {delay:.2f} seconds before navigating.")
                    await asyncio.sleep(delay)

                    first_item_before_locator = page.locator("#listDiv a .tx").first
                    first_item_before_text = await first_item_before_locator.inner_text()
                    await page.evaluate(f"movePage('{page_num}')")
                    await expect(first_item_before_locator).not_to_have_text(first_item_before_text, timeout=20000)
                    await page.wait_for_load_state('networkidle')
                    logger.info(f"Successfully navigated to page {page_num}.")

                onclick_list = []
                link_locators = await page.locator('#listDiv ul.left_list_bx li a').all()
                logger.info(f"Found {len(link_locators)} links on this page.")
                for link in link_locators:
                    onclick_attr = await link.get_attribute('onclick')
                    if onclick_attr:
                        onclick_list.append(onclick_attr)

                for i, onclick_attr in enumerate(onclick_list):
                    current_processing_item = onclick_attr
                    logger.info(f"  - [{i+1}/{len(onclick_list)}] Processing item: {onclick_attr}")

                    js_function_call = onclick_attr.replace('javascript:', '').replace('return false;', '').strip()
                    match = re.search(r"(\d+)", js_function_call)
                    if not match:
                        logger.warning(f"    Could not extract doc_seq from: {js_function_call}")
                        continue
                    doc_seq = match.group(1)

                    logger.debug(f"    Executing JavaScript: {js_function_call}")
                    await page.evaluate(js_function_call)

                    detail_title_locator = page.locator('#contentBody h2')
                    await expect(detail_title_locator).to_be_visible(timeout=20000)
                    logger.debug("    Detail view loaded.")

                    # --- Data Extraction ---
                    doc_title = await detail_title_locator.inner_text()
                    subtitle_locator = page.locator('#contentBody div.subtit1')
                    subtitle = await subtitle_locator.inner_text() if await subtitle_locator.count() > 0 else ""
                    department, doc_number, doc_date = parse_subtitle(subtitle)
                    source_url = f"https://www.law.go.kr/LSW/cgmExpcInfoP.do?cgmExpcSeq={doc_seq}"

                    sections = {}
                    content_elements = await page.locator('#contentBody h4, #contentBody .pty4').all()
                    current_header = None
                    for element in content_elements:
                        tag_name = await element.evaluate('el => el.tagName.toLowerCase()')
                        element_id = await element.get_attribute('id')

                        if tag_name == 'h4' and element_id != 'expcNotice':
                            header_text = await element.inner_text()
                            current_header = header_text.strip().replace('【', '').replace('】', '')
                            if current_header and current_header not in sections:
                                sections[current_header] = ""
                        elif tag_name == 'p' and current_header:
                            paragraph_text = await element.inner_text()
                            sections[current_header] += paragraph_text.strip() + "\n"

                    for header, text in sections.items():
                        cleaned_text = ' '.join(text.strip().split())
                        sections[header] = cleaned_text

                    scraped_data = {
                        "doc_id": f"{dept_code}-{doc_seq}",
                        "doc_title": ' '.join(doc_title.strip().split()),
                        "department": department,
                        "doc_number": doc_number,
                        "doc_date": doc_date,
                        "source_url": source_url,
                        "sections": sections,
                    }

                    # --- 개별 파일로 저장 ---
                    safe_title = re.sub(r'[\\/*?:"<>|]', '_', doc_title)[:50]
                    individual_file_path = os.path.join(output_dir, f"{doc_seq}_{safe_title}.jsonl")
                    with open(individual_file_path, 'w', encoding='utf-8') as f:
                        f.write(json.dumps(scraped_data, ensure_ascii=False) + '\n')
                    logger.info(f"  - Saved to: {os.path.basename(individual_file_path)}")

                    await asyncio.sleep(random.uniform(0.5, 1.5))
                logger.info(f"--- Finished page {page_num} / {pages_to_crawl} ---")

        except Exception as e:
            logger.error(f"An error occurred while processing '{current_processing_item}': {e}", exc_info=True)
            debug_dir = os.path.join(project_root, 'debug')
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            screenshot_path = os.path.join(debug_dir, f'error_screenshot_{timestamp}.png')
            await page.screenshot(path=screenshot_path)
            logger.info(f"Error screenshot saved to: {screenshot_path}")

            html_path = os.path.join(debug_dir, f'error_page_{timestamp}.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(await page.content())
            logger.info(f"Error page HTML saved to: {html_path}")

        finally:
            await browser.close()
            logger.info("Scraping process finished.")

