import asyncio
from playwright.async_api import async_playwright, expect
import json
import re
import os
import sys
import math
from datetime import datetime

# 프로젝트 루트 경로 설정
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

# 스크레이퍼 타입에 맞는 로거 생성
logger = get_logger(__name__, scraper_type='interpretation')

def build_detail_url(onclick_attr: str):
    """
    onclick 속성값에서 행정해석 상세 페이지 URL을 생성합니다.
    다양한 javascript 함수(cgmExpcView, lsEmpViewWideAll 등)를 처리합니다.
    """
    match = re.search(r"(?:cgmExpcView|lsEmpViewWideAll|expcDetail)\('([^,']*)", onclick_attr or "")
    if not match:
        logger.warning(f"URL을 추출할 수 없는 onclick 속성 발견: {onclick_attr}")
        return None

    expc_seq = match.group(1)
    full_url = f"https://www.law.go.kr/LSW/cgmExpcInfoP.do?cgmExpcSeq={expc_seq}"
    return full_url

async def fetch_urls(start_url: str, dept_code: str, max_pages_arg: int | None):
    """행정해석 목록 페이지를 순회하며 상세 페이지 URL을 추출하여 반환합니다."""
    urls_found = []
    if not dept_code:
        logger.error("부처 코드가 제공되지 않아 스크레이핑을 중단합니다.")
        return urls_found

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"목록 페이지로 이동 중: {start_url}")
            await page.goto(start_url, wait_until='networkidle', timeout=60000)

            total_items_locator = page.locator("#writeNumDiv > strong")
            initial_total_items_text = await total_items_locator.inner_text(timeout=10000)

            logger.info("상세검색 패널을 엽니다.")
            await page.evaluate("dtlSchOpen('11')")

            logger.info(f"소관부처를 코드로 선택합니다: {dept_code}")
            correct_select_id = 'select#selectDtlCgmExpc'
            await page.wait_for_selector(correct_select_id, state='visible', timeout=5000)
            await page.select_option(correct_select_id, value=dept_code)

            logger.info("검색 버튼을 클릭하여 필터를 적용합니다.")
            await page.evaluate("newDtlEtcSearch('cgmExpcItmNm')")

            logger.info("필터가 적용된 페이지 로딩을 기다립니다...")
            await expect(total_items_locator).not_to_have_text(initial_total_items_text, timeout=30000)
            await page.wait_for_load_state('networkidle', timeout=20000)
            logger.info("필터 적용이 완료되었습니다.")

            total_pages = 1
            items_per_page = 50

            try:
                total_items_text = await total_items_locator.inner_text()
                total_items = int(re.sub(r'[^0-9]', '', total_items_text))

                if total_items > 0:
                    total_pages = math.ceil(total_items / items_per_page)
                    logger.info(f"총 {total_items}개 항목, {total_pages} 페이지를 확인했습니다.")
                else:
                    logger.warning("검색 결과가 없습니다. 스크레이핑을 종료합니다.")
                    return urls_found
            except Exception:
                logger.error("총 페이지 수를 계산하는 데 실패했습니다. 첫 페이지만 크롤링합니다.", exc_info=True)

            pages_to_crawl = total_pages
            if max_pages_arg is not None and 0 < max_pages_arg < total_pages:
                pages_to_crawl = max_pages_arg
                logger.info(f"사용자 설정에 따라 최대 {pages_to_crawl} 페이지만 크롤링합니다.")

            for page_num in range(1, pages_to_crawl + 1):
                logger.info(f"--- {page_num} / {pages_to_crawl} 페이지 처리 중 ---")
                if page_num > 1:
                    logger.info(f"{page_num} 페이지로 이동합니다...")
                    first_item_selector = "td.s_tit > a"
                    first_item_before = await page.locator(first_item_selector).first.inner_text()

                    await page.evaluate(f"movePage('{page_num}')")

                    await expect(page.locator(first_item_selector).first).not_to_have_text(first_item_before, timeout=20000)
                    logger.info("페이지 이동 완료.")

                case_links = await page.query_selector_all("td.s_tit > a[onclick*='lsEmpViewWideAll'], td.s_tit > a[onclick*='showExternalLink']")

                for link in case_links:
                    onclick = await link.get_attribute("onclick")
                    case_name_raw = await link.inner_text()
                    detail_url = build_detail_url(onclick)
                    if detail_url and case_name_raw:
                        # [수정] &nbsp; 등 비정상 공백을 표준 공백으로 변환하고, 연속 공백을 하나로 합침
                        cleaned_name = ' '.join(case_name_raw.split())

                        # 파일명으로 사용할 수 없는 특수문자 제거
                        safe_name = re.sub(r'[\\/*?:"<>|]', "", cleaned_name).strip()

                        urls_found.append({"name": safe_name, "url": detail_url})
                        # [수정] 깨끗하게 정리된 제목을 로그에 출력
                        logger.info(f"  - 발견: {safe_name}")
        except Exception as e:
            debug_dir = os.path.join(project_root, 'debug')
            os.makedirs(debug_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = os.path.join(debug_dir, f'error_screenshot_{timestamp}.png')
            html_path = os.path.join(debug_dir, f'error_page_{timestamp}.html')

            logger.info(f"에러 발생! 디버깅을 위해 스크린샷과 HTML을 저장합니다.")
            logger.info(f"  - 스크린샷: {screenshot_path}")
            logger.info(f"  - HTML: {html_path}")

            await page.screenshot(path=screenshot_path)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(await page.content())

            logger.error(f"목록 스크레이핑 중 에러 발생: {e}", exc_info=True)
        finally:
            await browser.close()

    return urls_found

