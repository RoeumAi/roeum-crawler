import asyncio
from playwright.async_api import async_playwright, TimeoutError
import sys
import os

# 프로젝트 루트 경로 설정
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

# 스크레이퍼 타입에 맞는 로거 생성
logger = get_logger(__name__, scraper_type='interpretation')

async def check_url_validity(url: str):
    """
    주어진 URL에 접속하여 행정해석 목록이 정상적으로 로드되는지 확인합니다.
    성공 시 True, 실패 시 False를 반환합니다.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            # 1. 페이지로 이동하고 네트워크가 안정될 때까지 기다립니다.
            logger.info(f"URL 유효성 검사를 위해 페이지로 이동합니다: {url}")
            await page.goto(url, wait_until='networkidle', timeout=30000)

            # 2. 목록의 총 건수를 표시하는 요소가 나타나는지 확인합니다.
            # 이 요소는 스크립트가 데이터를 로드한 후에 나타나므로, 페이지 로딩 성공의 좋은 지표가 됩니다.
            await page.wait_for_selector("#writeNumDiv > strong", state="visible", timeout=15000)

            logger.info(f"URL 유효성 검증 성공: {url}")
            return True
        except TimeoutError:
            logger.error(f"URL 유효성 검증 실패: 지정된 시간 내에 콘텐츠가 로드되지 않았습니다. URL: {url}")
            return False
        except Exception as e:
            logger.error(f"URL 검증 중 알 수 없는 에러 발생: {e}", exc_info=True)
            return False
        finally:
            await browser.close()
