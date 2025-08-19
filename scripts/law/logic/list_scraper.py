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

logger = get_logger(__name__)

def build_detail_url(onclick_attr: str):
    """onclick 속성값에서 lsiSeq, efYd 등을 추출하여 상세 페이지 URL을 생성합니다."""
    m = re.search(r"lsReturnSearch\((.*?)\)", onclick_attr or "")
    if not m:
        return None

    args = re.findall(r"'([^']*)'", m.group(1))
    efYd = next((x for x in args if re.fullmatch(r"\d{8}", x)), None)

    nums = [x for x in args if re.fullmatch(r"\d{5,}", x)]
    if efYd in nums:
        nums.remove(efYd)

    lsiSeq = nums[-1] if nums else None

    if not lsiSeq:
        return None

    url = f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={lsiSeq}"
    if efYd:
        url += f"&efYd={efYd}"

    return url

async def fetch_law_urls(start_url: str, max_pages_arg: int | None):
    """법령 목록 페이지를 순회하며 상세 페이지 URL을 추출하여 반환합니다."""
    urls_found = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"목록 페이지로 이동 중: {start_url}")
            await page.goto(start_url, wait_until='networkidle', timeout=60000)

            total_pages = 1
            try:
                pagination_container = page.locator("div.lef").first
                pagination_text = await pagination_container.inner_text(timeout=5000)

                # 정규표현식으로 "(현재페이지/전체페이지)" 형식에서 전체 페이지 추출
                match = re.search(r'\((\d+)/(\d+)\)', pagination_text)
                if match:
                    total_pages = int(match.group(2)) # 두 번째 그룹(\d+)이 전체 페이지 수
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
                    await page.evaluate(f"pageSearch('lsListDiv','{page_num}')")
                    await expect(page.locator("#resultTableDiv tbody tr:first-child a").first).not_to_have_text(first_item_before, timeout=20000)
                    logger.info("페이지 이동 완료.")

                law_links = await page.query_selector_all("#resultTableDiv a[onclick*='lsReturnSearch']")
                for link in law_links:
                    onclick = await link.get_attribute("onclick")
                    law_name = await link.inner_text()
                    detail_url = build_detail_url(onclick)
                    if detail_url and law_name:
                        safe_name = re.sub(r'[\\/*?:"<>|]', "", law_name).strip()
                        urls_found.append({"name": safe_name, "url": detail_url})
                        logger.info(f"  - 발견: {law_name}")
        except Exception as e:
            logger.error(f"목록 스크레이핑 중 에러 발생: {e}", exc_info=True)
        finally:
            await browser.close()

    return urls_found

    # if urls_found:
    #     output_filename = "urls.jsonl"
    #     with open(output_filename, 'w', encoding='utf-8') as f:
    #         for item in urls_found:
    #             f.write(json.dumps(item, ensure_ascii=False) + '\n')
    #     logger.info(f"총 {len(urls_found)}개의 URL을 '{output_filename}' 파일에 저장했습니다.")


# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="법령 목록 페이지에서 상세 법령 URL들을 추출합니다.")
#     parser.add_argument("start_url", help="크롤링을 시작할 법령 목록 페이지의 URL")
#     parser.add_argument("-p", "--max_pages", type=int, default=None, help="크롤링할 최대 페이지 수 (지정하지 않으면 감지된 전체 페이지를 크롤링)")
#
#     args = parser.parse_args()
#
#     asyncio.run(main(args.start_url, args.max_pages))
