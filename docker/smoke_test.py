"""이미지 빌드 직후 확인: 실행에 필요한 것이 ARM64 컨테이너 안에 다 있는지.

외부 사이트·DB에는 접속하지 않는다. CI(이미지 빌드 워크플로)에서
`docker run --rm --entrypoint python3 <이미지> docker/smoke_test.py` 로 돈다.
"""
import asyncio
import importlib
import os
import shutil
import subprocess
import sys

sys.path.insert(0, "/app")

from scripts.core.config import get_scraper_list  # noqa: E402


def check_scraper_modules() -> None:
    for name in get_scraper_list():
        importlib.import_module(f"scripts.{name}.logic.list_scraper")
        importlib.import_module(f"scripts.{name}.logic.scraper")
    print(f"ok scrapers: {', '.join(get_scraper_list())}")


def check_tokenizer_offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    from scripts.utils.tokenizer_utils import count_tokens
    assert count_tokens("근로기준법 제23조 해고 등의 제한") > 0
    print("ok tokenizer (offline)")


def check_tesseract() -> None:
    import pytesseract
    path = pytesseract.pytesseract.tesseract_cmd
    assert shutil.which(path) or os.path.exists(path), path
    langs = subprocess.run([path, "--list-langs"], capture_output=True, text=True, check=True).stdout
    assert "kor" in langs.split(), langs
    print(f"ok tesseract {path} (kor)")


async def check_chromium() -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("<p id='x'>법령</p>")
        assert await page.inner_text("#x") == "법령"
        await browser.close()
    print("ok chromium")


def check_curl_cffi() -> None:
    from curl_cffi import requests as curl_requests
    curl_requests.Session(impersonate="chrome110").close()
    print("ok curl_cffi")


def check_timezone() -> None:
    from datetime import datetime
    offset = datetime.now().astimezone().utcoffset()
    assert offset is not None and offset.total_seconds() == 9 * 3600, offset
    print("ok TZ Asia/Seoul")


if __name__ == "__main__":
    check_scraper_modules()
    check_tokenizer_offline()
    check_tesseract()
    asyncio.run(check_chromium())
    check_curl_cffi()
    check_timezone()
    print("smoke ok")
