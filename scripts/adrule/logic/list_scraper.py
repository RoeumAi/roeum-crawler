import asyncio
from playwright.async_api import async_playwright, expect
import json
import re
import argparse
import os
import sys

# --- 프로젝트 루트 경로 설정 (사용자 환경에 맞게 조정될 수 있음) ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# --- 로거 설정 ---
try:
    from scripts.utils.logger_config import get_logger
    logger = get_logger(__name__, scraper_type='adrule')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(name)s] - %(message)s')
    logger = logging.getLogger(__name__)
    logger.warning("logger_config 모듈을 찾을 수 없어 기본 로깅을 사용합니다.")


def build_detail_url(onclick_attr: str):
    """
    onclick 속성값에서 lsiSeq, efYd 등을 추출하여 상세 페이지 URL을 생성합니다.
    [최종 수정] 정규표현식으로 함수 인자 전체를 정확히 추출하여 모든 onclick 형식을 안정적으로 처리합니다.
    """
    # [핵심 수정] 법령명에 괄호가 포함된 경우를 대비하여, 함수 호출의 마지막 ')'까지 탐욕적으로(greedily) 매칭합니다.
    m = re.search(r"admRulReturnSearch\((.*)\);", onclick_attr or "")
    if not m:
        logger.debug(f"build_detail_url: 'admRulReturnSearch(...);' 패턴을 찾지 못했습니다: {onclick_attr}")
        return None
    content = m.group(1)

    # ''로 둘러싸인 모든 파라미터를 추출합니다.
    params = re.findall(r"'([^']*)'", content)

    # 8자리 숫자인 파라미터를 efYd로 찾습니다.
    efYd = next((p for p in params if re.fullmatch(r"\d{8}", p)), None)

    # 5자리 이상 숫자인 파라미터를 lsiSeq 후보로 찾습니다.
    lsiSeq_candidates = [p for p in params if re.fullmatch(r"\d{5,}", p)]

    # lsiSeq 후보 중에서 efYd가 있다면 제외합니다.
    if efYd and efYd in lsiSeq_candidates:
        lsiSeq_candidates.remove(efYd)

    # 후보가 없으면 lsiSeq를 찾을 수 없습니다.
    if not lsiSeq_candidates:
        logger.info(f"build_detail_url: lsiSeq 후보를 찾지 못했습니다. 파라미터: {params}")
        return None

    # 남은 후보 중 마지막 것을 lsiSeq로 간주합니다. (가장 일반적인 규칙)
    lsiSeq = lsiSeq_candidates[-1]

    url = f"https://www.law.go.kr/admRulInfoP.do?admRulSeq={lsiSeq}"

    return url

async def fetch_urls(start_url: str, max_pages_arg: int | None):
    """
    법령 목록 페이지를 순회하며 상세 페이지 URL을 추출하여 반환합니다.
    테이블의 각 행(tr)을 순회하여 누락을 방지하고, 페이지 전환 시 내용 변경을 명확히 확인합니다.
    """
    urls_found = []
    logger.info("스크레이핑을 시작합니다...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"시작 페이지로 이동 중: {start_url}")
            await page.goto(start_url, wait_until='networkidle', timeout=60000)

            total_pages = 1
            try:
                await page.wait_for_selector("div.lef", timeout=5000)
                pagination_container = page.locator("div.lef").first
                pagination_text = await pagination_container.inner_text(timeout=5000)
                match = re.search(r'\((\d+)/(\d+)\)', pagination_text)
                if match:
                    total_pages = int(match.group(2))
                logger.info(f"총 {total_pages} 페이지를 확인했습니다.")
            except Exception:
                logger.warning("페이지네이션 정보를 찾을 수 없어 1페이지만 크롤링합니다.")

            pages_to_crawl = total_pages
            if max_pages_arg is not None and max_pages_arg > 0 and max_pages_arg < total_pages:
                pages_to_crawl = max_pages_arg
                logger.info(f"사용자 설정에 따라 최대 {pages_to_crawl} 페이지만 크롤링합니다.")

            for page_num in range(1, pages_to_crawl + 1):
                logger.info(f"--- {page_num} / {pages_to_crawl} 페이지 처리 중 ---")

                if page_num > 1:
                    first_item_text_before = await page.locator("#resultAdmRulTableDiv tbody tr:first-child a").first.inner_text()
                    logger.info(f"{page_num} 페이지로 이동합니다...")
                    await page.evaluate(f"pageSearch('admRulListDiv','{page_num}')")
                    logger.info("페이지 내용이 갱신되기를 대기합니다...")
                    await expect(page.locator("#resultAdmRulTableDiv tbody tr:first-child a").first).not_to_have_text(first_item_text_before, timeout=30000)
                    logger.info("페이지 내용 갱신 확인 및 로딩 완료.")

                rows = await page.query_selector_all("#resultAdmRulTableDiv tbody tr")

                num_links_found_this_page = 0
                num_links_added_this_page = 0
                for row in rows:
                    link = await row.query_selector("a[onclick*='admRulReturnSearch']")

                    if link:
                        num_links_found_this_page += 1
                        onclick = await link.get_attribute("onclick")
                        law_name = await link.inner_text()
                        detail_url = build_detail_url(onclick)

                        if detail_url and law_name:
                            safe_name = re.sub(r'[\\/*?:"<>|]', "", law_name).strip()
                            urls_found.append({"name": safe_name, "url": detail_url})
                            num_links_added_this_page += 1
                        else:
                            logger.warning(
                                f"!!! 누락 경고 ({page_num}페이지 {num_links_found_this_page}번째 항목): URL 또는 법령명을 추출하지 못했습니다."
                            )
                            logger.warning(f"    - 법령명: '{law_name}'")
                            logger.warning(f"    - OnClick 속성: '{onclick}'")
                            if not detail_url:
                                logger.warning("    - 추정 원인: build_detail_url 함수가 유효한 URL을 생성하지 못했습니다.")

                logger.info(f"현재 페이지에서 {num_links_found_this_page}개의 링크를 발견, {num_links_added_this_page}개를 최종 추가했습니다.")
                if page_num < pages_to_crawl and num_links_added_this_page < 50 and num_links_added_this_page > 0:
                    logger.warning(
                        f"!!! 페이지 요약 경고: {page_num} 페이지에서 예상(50개)보다 적은 {num_links_added_this_page}개의 URL이 추가되었습니다."
                    )

        except Exception as e:
            logger.error(f"스크레이핑 중 예상치 못한 에러 발생: {e}", exc_info=True)
        finally:
            logger.info("브라우저를 닫습니다.")
            await browser.close()

    logger.info(f"스크레이핑 완료. 총 {len(urls_found)}개의 URL을 수집했습니다.")
    return urls_found

async def main(start_url: str, max_pages: int | None):
    """메인 실행 함수"""
    urls = await fetch_urls(start_url, max_pages)
    if urls:
        output_filename = "urls.jsonl"
        with open(output_filename, 'w', encoding='utf-8') as f:
            for item in urls:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        logger.info(f"추출된 URL 목록을 '{output_filename}' 파일에 저장했습니다.")
    else:
        logger.warning("추출된 URL이 없습니다.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="법제처 행정규칙 목록 페이지에서 상세 URL을 추출합니다.")
    parser.add_argument("start_url", help="크롤링을 시작할 법령 목록 페이지의 URL")
    parser.add_argument("-p", "--max_pages", type=int, default=None, help="크롤링할 최대 페이지 수 (기본값: 전체 페이지)")

    args = parser.parse_args()

    asyncio.run(main(args.start_url, args.max_pages))

