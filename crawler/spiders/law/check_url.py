import asyncio
from playwright.async_api import async_playwright, TimeoutError
import argparse

async def check_url_validity(url: str):
    """
    주어진 URL에 접속하여 법령 목록이 정상적으로 로드되는지 확인합니다.
    성공 시 True, 실패 시 False를 반환합니다.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=20000)

            # 로딩 마스크가 사라질 때까지 대기
            await page.wait_for_selector(".loadmask-msg", state="visible", timeout=10000)
            await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=30000)

            # 법령 목록 테이블의 링크가 하나라도 존재하는지 확인
            await page.wait_for_selector("#resultTableDiv a[onclick*='lsReturnSearch']", state="visible", timeout=10000)

            print("✅ URL 유효성 검증 성공: 법령 목록을 확인했습니다.")
            return True
        except TimeoutError:
            print(f"❌ URL 유효성 검증 실패: 해당 페이지에서 법령 목록을 찾을 수 없거나 로드에 실패했습니다.")
            return False
        except Exception as e:
            print(f"❌ 알 수 없는 에러 발생: {e}")
            return False
        finally:
            await browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="법령 목록 URL의 유효성을 검증합니다.")
    parser.add_argument("url", help="검증할 법령 목록 페이지의 전체 URL")
    args = parser.parse_args()

    is_valid = asyncio.run(check_url_validity(args.url))

    # 쉘 스크립트가 판단할 수 있도록 종료 코드를 반환
    if is_valid:
        exit(0) # 성공
    else:
        exit(1) # 실패