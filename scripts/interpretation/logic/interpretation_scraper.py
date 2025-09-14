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
# __file__이 정의되지 않은 환경(예: Jupyter notebook)을 위해 경로를 안전하게 설정합니다.
try:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
except NameError:
    project_root = os.path.abspath('.') # 현재 작업 디렉토리를 기준으로 설정
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

# 스크레이퍼 타입에 맞는 로거 생성
logger = get_logger(__name__, scraper_type='interpretation')

def parse_subtitle(subtitle_text):
    """부제목 text에서 부서, 문서번호, 날짜를 추출합니다."""
    logger.debug(f"Subtitle 파싱 시도: {subtitle_text}")
    date_pattern = r'(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})\.'

    match = re.search(r'\[\s*(.*?)\s+(.*?),\s*' + date_pattern + r'\s*\]', subtitle_text)
    if match:
        department = match.group(1).strip()
        doc_number = match.group(2).strip()
        doc_date = match.group(3).strip().replace(" ", "")
        logger.debug(f"패턴 1로 파싱 성공: 부서='{department}', 번호='{doc_number}', 날짜='{doc_date}'")
        return department, doc_number, doc_date

    match = re.search(r'\[\s*(.*?),\s*' + date_pattern + r'\s*\]', subtitle_text)
    if match:
        department = match.group(1).strip()
        doc_date = match.group(2).strip().replace(" ", "")
        logger.debug(f"패턴 2로 파싱 성공: 부서='{department}', 번호='정보 없음', 날짜='{doc_date}'")
        return department, "정보 없음", doc_date

    match = re.search(r'\[(.*?)\]', subtitle_text)
    if match:
        department = match.group(1).strip()
        logger.debug(f"패턴 3(Fallback)으로 파싱 성공: 부서='{department}', 번호/날짜 없음")
        return department, "정보 없음", "정보 없음"

    logger.warning(f"Subtitle 파싱 실패: {subtitle_text}")
    return "정보 없음", "정보 없음", "정보 없음"

async def scrape_interpretation_data(start_url: str, dept_code: str, output_dir: str, max_pages_arg: int | None):
    """
    필터링, 목록 순회, RAG용 상세 정보 추출까지 모두 수행하여 개별 파일로 저장합니다.
    오류 발생 시에도 중단되지 않고 다음 항목을 계속 처리합니다.
    """
    logger.info("스크레이핑 프로세스를 시작합니다.")
    os.makedirs(output_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            logger.info(f"시작 페이지로 이동합니다: {start_url}")
            await page.goto(start_url, wait_until='networkidle', timeout=60000)
            logger.info("페이지 이동 완료.")

            total_items_locator = page.locator("#writeNumDiv > strong")
            initial_total_items_text = await total_items_locator.inner_text(timeout=10000)
            logger.info(f"초기 총 항목 텍스트: '{initial_total_items_text.strip()}'")

            logger.info("상세검색 패널을 엽니다.")
            await page.evaluate("dtlSchOpen('11')")

            logger.info(f"부처 코드를 선택합니다: {dept_code}")
            await page.wait_for_selector('select#selectDtlCgmExpc', state='visible', timeout=10000)
            await page.select_option('select#selectDtlCgmExpc', value=dept_code)

            logger.info("검색 버튼을 클릭하여 필터를 적용합니다.")
            await page.evaluate("newDtlEtcSearch('cgmExpcItmNm')")

            logger.info("필터 적용 후 페이지가 업데이트되기를 기다립니다...")
            await expect(total_items_locator).not_to_have_text(initial_total_items_text, timeout=30000)
            await page.wait_for_load_state('networkidle', timeout=30000)
            logger.info("필터 적용 완료.")

            total_pages = 1
            try:
                total_items_text = await total_items_locator.inner_text()
                total_items = int(re.sub(r'[^0-9]', '', total_items_text))
                total_pages = math.ceil(total_items / 50)
                logger.info(f"총 {total_items}개 항목, {total_pages} 페이지를 확인했습니다.")
            except Exception as e:
                logger.error(f"총 페이지 수 계산 실패: {e}", exc_info=True)
                await browser.close()
                return

            pages_to_crawl = total_pages
            if max_pages_arg is not None and 0 < max_pages_arg < total_pages:
                pages_to_crawl = max_pages_arg
                logger.info(f"사용자 설정에 따라 최대 {pages_to_crawl} 페이지만 크롤링합니다.")

            for page_num in range(1, pages_to_crawl + 1):
                logger.info(f"--- 페이지 처리 시작: {page_num} / {pages_to_crawl} ---")
                if page_num > 1:
                    logger.info(f"{page_num} 페이지로 이동합니다...")
                    delay = random.uniform(1.0, 2.5)
                    logger.debug(f"페이지 이동 전 {delay:.2f}초 대기합니다.")
                    await asyncio.sleep(delay)

                    first_item_before_locator = page.locator("#listDiv a .tx").first
                    first_item_before_text = await first_item_before_locator.inner_text()
                    await page.evaluate(f"movePage('{page_num}')")

                    loading_mask_locator = page.locator('.loadmask')
                    if await loading_mask_locator.is_visible(timeout=500):
                        logger.debug("페이지 이동 로딩 마스크 감지. 사라질 때까지 대기합니다.")
                        await expect(loading_mask_locator).to_be_hidden(timeout=30000)

                    await expect(first_item_before_locator).not_to_have_text(first_item_before_text, timeout=20000)
                    logger.info(f"{page_num} 페이지로 성공적으로 이동했습니다.")

                onclick_list = await page.locator('#listDiv ul.left_list_bx li a').evaluate_all(
                    "elements => elements.map(el => el.getAttribute('onclick'))"
                )
                logger.info(f"현재 페이지에서 {len(onclick_list)}개의 클릭 가능한 항목을 찾았습니다.")

                for i, onclick_attr in enumerate(onclick_list):
                    if not onclick_attr:
                        continue

                    # ⭐ 개별 항목 처리를 위한 try-except 블록 추가
                    try:
                        logger.info(f"  - [{i+1}/{len(onclick_list)}] 항목 처리 중: {onclick_attr}")

                        js_function_call = onclick_attr.replace('javascript:', '').replace('return false;', '').strip()
                        match = re.search(r"(\d+)", js_function_call)
                        if not match:
                            logger.warning(f"    ID를 추출할 수 없습니다: {js_function_call}")
                            continue
                        doc_seq = match.group(1)

                        logger.debug(f"    JavaScript 실행: {js_function_call}")
                        await page.evaluate(js_function_call)

                        loading_mask_locator = page.locator('.loadmask')
                        if await loading_mask_locator.is_visible(timeout=500):
                            logger.info("    상세 정보 로딩 마스크 감지. 사라질 때까지 대기합니다.")
                            await expect(loading_mask_locator).to_be_hidden(timeout=50000)

                        # ⭐ [수정] 제목 비교 대신, 고유 ID 값으로 페이지 업데이트를 확인
                        detail_view_id_locator = page.locator('input#cgmExpcDatSeq')
                        await expect(detail_view_id_locator).to_have_value(doc_seq, timeout=30000)
                        logger.debug(f"    상세 정보 ID({doc_seq}) 업데이트를 확인했습니다.")

                        doc_title = await page.locator('#contentBody h2').inner_text()
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

                        safe_title = re.sub(r'[\\/*?:"<>|]', '_', doc_title)[:50]
                        individual_file_path = os.path.join(output_dir, f"{doc_seq}_{safe_title}.jsonl")
                        with open(individual_file_path, 'w', encoding='utf-8') as f:
                            f.write(json.dumps(scraped_data, ensure_ascii=False) + '\n')
                        logger.info(f"  - 저장 완료: {os.path.basename(individual_file_path)}")

                        await asyncio.sleep(random.uniform(0.5, 1.5))

                    except Exception as e:
                        # ⭐ 개별 항목 에러 처리
                        logger.error(f"  - [{i+1}/{len(onclick_list)}] 항목 처리 중 에러 발생 '{onclick_attr}': {e}", exc_info=True)

                        debug_dir = os.path.join(project_root, 'debug', 'item_errors')
                        os.makedirs(debug_dir, exist_ok=True)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        safe_onclick = re.sub(r'[\\/*?:"<>|]', '_', onclick_attr)[:100]

                        screenshot_path = os.path.join(debug_dir, f'item_error_screenshot_{safe_onclick}_{timestamp}.png')
                        html_path = os.path.join(debug_dir, f'item_error_page_{safe_onclick}_{timestamp}.html')

                        try:
                            await page.screenshot(path=screenshot_path)
                            logger.warning(f"    에러 스크린샷 저장: {screenshot_path}")
                            with open(html_path, 'w', encoding='utf-8') as f:
                                f.write(await page.content())
                            logger.warning(f"    에러 HTML 저장: {html_path}")
                        except Exception as debug_e:
                            logger.error(f"    디버그 파일 저장 중 추가 에러 발생: {debug_e}")

                        logger.info("    다음 항목으로 계속 진행합니다.")
                        continue # ⭐ 다음 항목으로 넘어가기

                logger.info(f"--- 페이지 처리 완료: {page_num} / {pages_to_crawl} ---")

        except Exception as e:
            # 전체 프로세스에 영향을 주는 큰 에러 처리
            logger.critical(f"스크레이핑 프로세스 중 심각한 오류 발생: {e}", exc_info=True)
            debug_dir = os.path.join(project_root, 'debug')
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            screenshot_path = os.path.join(debug_dir, f'fatal_error_screenshot_{timestamp}.png')
            await page.screenshot(path=screenshot_path)
            logger.info(f"에러 스크린샷 저장: {screenshot_path}")

            html_path = os.path.join(debug_dir, f'fatal_error_page_{timestamp}.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(await page.content())
            logger.info(f"에러 HTML 저장: {html_path}")

        finally:
            await browser.close()
            logger.info("스크레이핑 프로세스를 종료합니다.")