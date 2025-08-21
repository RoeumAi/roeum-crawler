import asyncio
from playwright.async_api import async_playwright, TimeoutError
import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='adrule')

async def check_url_validity(url: str):
    """
    주어진 URL에 접속하여 행정규칙 목록이 정상적으로 로드되는지 확인합니다.
    성공 시 True, 실패 시 False를 반환합니다.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=20000)
            await page.wait_for_selector(".loadmask-msg", state="visible", timeout=10000)
            await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=30000)
            await page.wait_for_selector("#admRulResultTable a[onclick*='admRulReturnSearch']", state="visible", timeout=10000)
            logger.info(f"URL 유효성 검증 성공: {url}")
            return True
        except TimeoutError:
            logger.error(f"URL 유효성 검증 실패 TimeoutERROR: {url}")
            return False
        except Exception as e:
            logger.error(f"URL 검증 중 알 수 없는 에러 발생: {e}", exc_info=True)
            return False
        finally:
            await browser.close()

# ⭐️ 2. if __name__ == "__main__": 블록 전체를 삭제하여 순수 모듈로 만듭니다.
