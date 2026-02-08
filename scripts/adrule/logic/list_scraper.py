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
    onclick 속성값에서 ID(admRulSeq)를 추출하여 상세 페이지 URL을 생성합니다.
    
    신형 패턴: admRulReturnSearch('법령명','ID','Y또는N');
    구형 패턴: admRulReturnSearch(여러파라미터들);
    
    둘 다 지원합니다.
    """
    if not onclick_attr:
        return None
    
    # admRulReturnSearch(...); 패턴 매칭
    m = re.search(r"admRulReturnSearch\((.*?)\);", onclick_attr)
    if not m:
        logger.debug(f"build_detail_url: 'admRulReturnSearch(...);' 패턴을 찾지 못했습니다: {onclick_attr}")
        return None
    
    content = m.group(1)
    
    # ''로 둘러싸인 모든 파라미터를 추출합니다.
    params = re.findall(r"'([^']*)'", content)
    
    if not params:
        logger.debug(f"build_detail_url: 파라미터를 찾지 못했습니다: {content}")
        return None
    
    # 신형 패턴: admRulReturnSearch('법령명','ID','Y또는N')
    # 신형은 정확히 3개의 파라미터를 가짐 (법령명, ID, Y/N)
    if len(params) == 3:
        admrul_id = params[1]  # 두 번째 파라미터가 ID
        if re.fullmatch(r"\d+", admrul_id):  # ID는 숫자
            url = f"https://www.law.go.kr/admRulInfoP.do?admRulSeq={admrul_id}"
            logger.debug(f"build_detail_url (신형): ID={admrul_id}")
            return url
    
    # 구형 패턴: 여러 파라미터 중에서 ID 찾기
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
    
    # 남은 후보 중 마지막 것을 lsiSeq로 간주합니다.
    lsiSeq = lsiSeq_candidates[-1]
    url = f"https://www.law.go.kr/admRulInfoP.do?admRulSeq={lsiSeq}"
    logger.debug(f"build_detail_url (구형): lsiSeq={lsiSeq}")
    
    return url

async def fetch_urls(start_url: str, max_pages_arg: int | None):
    """법령 목록 페이지를 순회하며 상세 페이지 URL을 추출하여 반환합니다. (adrule용)"""
    urls_found = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"시작 페이지로 이동 중: {start_url}")
            await page.goto(start_url, wait_until='networkidle', timeout=60000)

            total_pages = 1
            try:
                # 새로운 URL 구조에서 페이지네이션 정보 찾기
                # 여러 선택자를 시도합니다
                pagination_text = None
                
                # 먼저 div.lef 시도 (기존 방식)
                try:
                    pagination_container = page.locator("div.lef").first
                    pagination_text = await pagination_container.inner_text(timeout=5000)
                except:
                    pass
                
                # 실패하면 다른 pagination 요소 찾기
                if not pagination_text:
                    try:
                        # 페이지네이션 정보가 있을 만한 다른 요소들 시도
                        pagination_text = await page.inner_text(".paginate_area, .paging, div[class*='paging']", timeout=5000)
                    except:
                        pass

                if pagination_text:
                    # 정규표현식으로 "(현재페이지/전체페이지)" 형식에서 전체 페이지 추출
                    match = re.search(r'\((\d+)/(\d+)\)', pagination_text)
                    if match:
                        total_pages = int(match.group(2)) # 두 번째 그룹(\d+)이 전체 페이지 수
                        logger.info(f"총 {total_pages} 페이지를 확인했습니다.")
                    else:
                        logger.warning("페이지네이션 형식을 파싱할 수 없습니다.")
                else:
                    logger.warning("페이지네이션 정보를 찾을 수 없어 1페이지만 크롤링합니다.")
            except Exception as e:
                logger.warning(f"페이지네이션 정보 추출 중 오류: {e}")

            pages_to_crawl = total_pages
            if max_pages_arg is not None and max_pages_arg < total_pages:
                pages_to_crawl = max_pages_arg
                logger.info(f"사용자 설정에 따라 최대 {pages_to_crawl} 페이지만 크롤링합니다.")

            for page_num in range(1, pages_to_crawl + 1):
                logger.info(f"--- {page_num} / {pages_to_crawl} 페이지 처리 중 ---")
                if page_num > 1:
                    logger.info(f"{page_num} 페이지로 이동합니다...")
                    try:
                        # 테이블의 첫 번째 행 텍스트 저장
                        first_item_before = await page.locator("#admRulResultTable tbody tr:first-child a").first.inner_text()
                        # pageSearch 함수 호출로 다음 페이지로 이동
                        await page.evaluate(f"pageSearch('admRulListDiv','{page_num}')")
                        # 페이지 변경 확인
                        await expect(page.locator("#admRulResultTable tbody tr:first-child a").first).not_to_have_text(first_item_before, timeout=20000)
                        logger.info("페이지 이동 완료.")
                    except Exception as e:
                        logger.error(f"페이지 이동 실패: {e}")
                        break

                # 새로운 구조: admRulResultTable에서 링크 추출
                adrule_links = await page.query_selector_all("#admRulResultTable a[onclick*='admRulReturnSearch']")
                logger.info(f"이 페이지에서 {len(adrule_links)}개의 링크를 발견했습니다.")
                
                for link in adrule_links:
                    try:
                        onclick = await link.get_attribute("onclick")
                        adrule_name = await link.inner_text()
                        detail_url = build_detail_url(onclick)
                        if detail_url and adrule_name:
                            safe_name = re.sub(r'[\\/*?:"<>|]', "", adrule_name).strip()
                            urls_found.append({"name": safe_name, "url": detail_url})
                            logger.info(f"  - 발견: {adrule_name}")
                    except Exception as e:
                        logger.error(f"링크 처리 중 오류: {e}")
                        continue
        except Exception as e:
            logger.error(f"목록 스크레이핑 중 에러 발생: {e}", exc_info=True)
        finally:
            await browser.close()

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

