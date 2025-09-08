import asyncio
from playwright.async_api import async_playwright, expect
import json
import re
import argparse
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')

def build_detail_url(onclick_attr: str):
    """onclick 속성값에서 상세 페이지 URL을 생성합니다."""
    match = re.search(r"openDetail\('([^']*)'\)", onclick_attr or "")
    if not match:
        logger.warning(f"URL을 추출할 수 없는 onclick 속성 발견: {onclick_attr}")
        return None

    relative_url = match.group(1)
    full_url = f"https://www.law.go.kr/LSW/{relative_url}"
    return full_url

async def fetch_urls(start_url: str, max_pages_arg: int | None):
    """판례 목록 페이지를 순회하며 상세 페이지 URL을 추출하여 반환합니다."""
    urls_found = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"목록 페이지로 이동 중: {start_url}")
            await page.goto(start_url, wait_until='networkidle', timeout=60000)

            total_pages = 1
            try:
                # [수정] 총 페이지 수를 포함하는 올바른 선택자로 변경
                pagination_container = page.locator("div.tit2").first
                pagination_text = await pagination_container.inner_text(timeout=5000)

                match = re.search(r'\((\d+)/(\d+)\)', pagination_text)
                if match:
                    total_pages = int(match.group(2))
                logger.info(f"총 {total_pages} 페이지를 확인했습니다.")
            except Exception:
                logger.warning("페이지네이션 정보를 찾을 수 없어 1페이지만 크롤링합니다.")

            pages_to_crawl = total_pages
            if max_pages_arg is not None and max_pages_arg < total_pages:
                pages_to_crawl = max_pages_arg
                logger.info(f"사용자 설정에 따라 최대 {pages_to_crawl} 페이지만 크롤링합니다.")

            for page_num in range(1, pages_to_crawl + 1):
                logger.info(f"--- {page_num} / {pages_to_crawl} 페이지 처리 중 ---")
                if page_num > 1:
                    logger.info(f"{page_num} 페이지로 이동합니다...")
                    first_item_before = await page.locator("#resultTableDiv tbody tr:first-child a").first.inner_text()

                    # [수정] 올바른 페이지 이동 JS 함수 호출
                    await page.evaluate(f"pageSearch('lsListDiv', '{page_num}')")

                    await expect(page.locator("#resultTableDiv tbody tr:first-child a").first).not_to_have_text(first_item_before, timeout=20000)
                    logger.info("페이지 이동 완료.")

                case_links = await page.query_selector_all("#resultTableDiv a[onclick*='precInfoP.do']")
                for link in case_links:
                    onclick = await link.get_attribute("onclick")
                    case_name = await link.inner_text()
                    detail_url = build_detail_url(onclick)
                    if detail_url and case_name:
                        safe_name = re.sub(r'[\\/*?:"<>|]', "", case_name).strip()
                        urls_found.append({"name": safe_name, "url": detail_url})
                        logger.info(f"  - 발견: {case_name}")
        except Exception as e:
            logger.error(f"목록 스크레이핑 중 에러 발생: {e}", exc_info=True)
        finally:
            await browser.close()

    return urls_found

async def main():
    """스크립트 실행을 위한 메인 함수."""
    parser = argparse.ArgumentParser(description="판례 목록 페이지에서 상세 URL들을 추출합니다.")
    parser.add_argument("start_url", help="크롤링을 시작할 목록 페이지의 URL")
    parser.add_argument("-o", "--output", required=True, help="추출된 URL을 저장할 파일 경로 (예: urls.jsonl)")
    parser.add_argument("--max_pages", type=int, default=None, help="크롤링할 최대 페이지 수")
    args = parser.parse_args()

    urls = await fetch_urls(args.start_url, args.max_pages)

    if urls:
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        with open(args.output, 'w', encoding='utf-8') as f:
            for item in urls:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        logger.info(f"총 {len(urls)}개의 URL을 '{args.output}' 파일에 저장했습니다.")

if __name__ == "__main__":
    asyncio.run(main())

