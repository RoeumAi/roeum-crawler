import asyncio
from playwright.async_api import async_playwright, expect
import json
import re
import math
import os
import sys

# 프로젝트 루트 경로 설정
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """
    법령해석 목록 페이지를 순회하며 상세 페이지 URL을 추출하여 반환합니다.
    
    Args:
        start_url: 시작 URL
        max_pages_arg: 최대 페이지 수 (None이면 전체)
    
    Returns:
        list: [{"name": "문서명", "url": "상세페이지URL"}, ...] 형식의 리스트
    """
    urls_found = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            logger.info(f"시작 페이지로 이동 중: {start_url}")
            await page.goto(start_url, wait_until='networkidle', timeout=60000)
            
            # 총 페이지 수 계산
            total_pages = 1
            try:
                total_items_locator = page.locator("#writeNumDiv > strong")
                total_items_text = await total_items_locator.inner_text(timeout=10000)
                total_items = int(re.sub(r'[^0-9]', '', total_items_text))
                total_pages = math.ceil(total_items / 50)
                logger.info(f"총 {total_items}개 항목, {total_pages} 페이지를 확인했습니다.")
            except Exception as e:
                logger.error(f"총 페이지 수 계산 실패: {e}", exc_info=True)
                await browser.close()
                return []
            
            # 크롤링할 페이지 수 결정
            pages_to_crawl = total_pages
            if max_pages_arg is not None and 0 < max_pages_arg < total_pages:
                pages_to_crawl = max_pages_arg
                logger.info(f"사용자 설정에 따라 최대 {pages_to_crawl} 페이지만 크롤링합니다.")
            
            # 페이지 순회
            for page_num in range(1, pages_to_crawl + 1):
                logger.info(f"--- 페이지 {page_num} / {pages_to_crawl} 처리 중 ---")
                
                if page_num > 1:
                    logger.info(f"{page_num} 페이지로 이동합니다...")
                    await asyncio.sleep(1.5)  # 페이지 이동 대기
                    
                    first_item_before_locator = page.locator("#listDiv a .tx").first
                    first_item_before_text = await first_item_before_locator.inner_text()
                    await page.evaluate(f"movePage('{page_num}')")
                    
                    # 로딩 마스크 대기
                    loading_mask_locator = page.locator('.loadmask')
                    if await loading_mask_locator.is_visible(timeout=500):
                        logger.debug("페이지 이동 로딩 마스크 감지. 사라질 때까지 대기합니다.")
                        await expect(loading_mask_locator).to_be_hidden(timeout=30000)
                    
                    await expect(first_item_before_locator).not_to_have_text(first_item_before_text, timeout=20000)
                    logger.info(f"{page_num} 페이지로 성공적으로 이동했습니다.")
                
                # onclick 속성 수집
                onclick_list = await page.locator('#listDiv ul.left_list_bx li a').evaluate_all(
                    "elements => elements.map(el => el.getAttribute('onclick'))"
                )
                logger.info(f"현재 페이지에서 {len(onclick_list)}개의 클릭 가능한 항목을 찾았습니다.")
                
                # onclick 속성 추출
                for onclick_attr in onclick_list:
                    if not onclick_attr:
                        continue
                    
                    # onclick 속성에서 ID 추출: cgmExpcView('2286051', '350101')
                    match = re.search(r"cgmExpcView\('(\d+)',\s*'(\d+)'\)", onclick_attr)
                    if not match:
                        logger.warning(f"onclick에서 ID를 추출할 수 없습니다: {onclick_attr}")
                        continue
                    
                    doc_seq = match.group(1)
                    ofi_cls_cd = match.group(2)
                    
                    # onclick 속성을 그대로 전달 (scraper.py에서 실행)
                    urls_found.append({
                        "name": f"interpretation_{doc_seq}",
                        "onclick": onclick_attr,
                        "doc_seq": doc_seq,
                        "ofi_cls_cd": ofi_cls_cd
                    })
                
                logger.info(f"페이지 {page_num} 처리 완료. 현재까지 {len(urls_found)}개 URL 수집됨")
            
            logger.info(f"✅ 전체 URL 수집 완료: {len(urls_found)}개")
            return urls_found
        
        except Exception as e:
            logger.error(f"❌ URL 수집 중 오류 발생: {e}", exc_info=True)
            return []
        
        finally:
            await browser.close()


if __name__ == "__main__":
    # 테스트 실행
    import argparse
    
    parser = argparse.ArgumentParser(description="법령해석 URL 수집기")
    parser.add_argument("--dept_code", default="1492000", help="부처 코드 (기본값: 1492000)")
    parser.add_argument("--max_pages", type=int, default=None, help="최대 페이지 수")
    args = parser.parse_args()
    
    start_url = f"https://www.law.go.kr/LSW/cgmExpcSc.do?menuId=11&subMenuId=729&tabMenuId=733"
    
    urls = asyncio.run(fetch_urls(start_url, args.max_pages))
    
    print(f"\n수집된 URL: {len(urls)}개")
    for i, url_item in enumerate(urls[:5], 1):
        print(f"{i}. {url_item['name']}: {url_item['url']}")
