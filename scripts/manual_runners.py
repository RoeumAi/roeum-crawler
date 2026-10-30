"""수동 실행용 범용 러너 — 종류(kind)별 표 하나로 `scrape` / `list` / `check-url` 을 처리한다.

`manual.py` 가 `scrape:law` 같은 작업 이름을 `scrape law ...` 로 바꿔 이 모듈에 넘긴다.
직접 쓰려면: `python3 -m scripts.manual_runners scrape law <URL> -d <부처코드> -o <이름> --no-db`

새 종류를 더하려면 아래 표(SCRAPE_KINDS, LIST_KINDS, CHECK_URL_KINDS)에 한 줄을 넣고
manual.py 의 TASKS 와 docs/manual-tasks.md 에도 한 줄 적는다.
"""
import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from scripts.utils.logger_config import get_logger  # noqa: E402


@dataclass(frozen=True)
class ScrapeKind:
    label: str                      # 도움말에 쓰는 이름
    needs_dept: bool                # -d(저장 하위 폴더) 필수 여부
    pass_dept_code: bool            # scrape_and_save 에 dept_code 를 넘길지
    extra: dict = field(default_factory=dict)  # scrape_and_save 에 더 넘길 인자


SCRAPE_KINDS = {
    "law": ScrapeKind("법령", True, False),
    "adrule": ScrapeKind("행정규칙", True, True, {"save_jsonl": True}),
    "case": ScrapeKind("판례", True, True, {"process_chunks": True}),
    "judgment": ScrapeKind("주요판정사례", False, False, {"save_jsonl": True}),
}

# 목록 수집: 판례만 start_url 에서 cptOfiCd 를 읽는다(없으면 기본 부처). 나머지는 Open API 라 URL 이 필요 없다.
LIST_KINDS = {
    "adrule": ("행정규칙", False),
    "case": ("판례", True),
    "judgment": ("주요판정사례", False),
}


@dataclass(frozen=True)
class CheckUrlKind:
    label: str
    wait_until: str
    goto_timeout_ms: int
    selector: str
    selector_timeout_ms: int
    wait_loadmask: bool = False


CHECK_URL_KINDS = {
    "law": CheckUrlKind("법령 목록", "domcontentloaded", 20000,
                        "#resultTableDiv a[onclick*='lsReturnSearch']", 10000, wait_loadmask=True),
    "interpretation": CheckUrlKind("해석례 상세", "networkidle", 30000,
                                   "#writeNumDiv > strong", 15000),
}


def _run_dir(*parts: str) -> str:
    return os.path.join(PROJECT_ROOT, "data", "raw", *parts)


async def run_scrape(kind: str, argv: list[str]) -> int:
    spec = SCRAPE_KINDS[kind]
    parser = argparse.ArgumentParser(
        prog=f"manual.py scrape:{kind}",
        description=f"{spec.label} 상세 페이지 하나를 스크레이핑해 파일과 MongoDB 에 저장한다.")
    parser.add_argument("url", help=f"스크레이핑할 {spec.label} 상세 페이지 URL")
    if spec.needs_dept:
        parser.add_argument("-d", "--dept", required=True, help="데이터를 저장할 하위 폴더 이름 (보통 부처 코드)")
    parser.add_argument("-o", "--output", required=True, help="출력 파일의 기본 이름 (확장자 제외)")
    parser.add_argument("--no-db", action="store_true", help="MongoDB 저장하지 않음 (기본값: 저장함)")
    args = parser.parse_args(argv)

    scrape_and_save = import_module(f"scripts.{kind}.logic.scraper").scrape_and_save
    logger = get_logger(__name__, scraper_type=kind)
    dept = getattr(args, "dept", None)
    output_dir = _run_dir(kind, dept) if dept else _run_dir(kind)

    kwargs = dict(spec.extra)
    if spec.pass_dept_code:
        kwargs["dept_code"] = dept
    logger.info(f"상세 페이지 스크레이퍼 실행: {args.url}")
    await scrape_and_save(args.url, output_dir, args.output, save_to_db=not args.no_db, **kwargs)
    logger.info(f"상세 페이지 스크레이퍼 완료: {args.output}")
    return 0


async def run_list(kind: str, argv: list[str]) -> int:
    label, uses_start_url = LIST_KINDS[kind]
    parser = argparse.ArgumentParser(
        prog=f"manual.py list:{kind}", description=f"{label} 목록에서 상세 URL 을 뽑아 JSONL 로 저장한다.")
    if uses_start_url:
        parser.add_argument("start_url", nargs="?", default="",
                            help="cptOfiCd 가 들어 있는 시작 URL (없으면 기본 부처)")
    parser.add_argument("-p", "--max_pages", type=int, default=None, help="수집할 최대 페이지 수 (없으면 전체)")
    parser.add_argument("-o", "--output", required=True, help="결과를 저장할 JSONL 파일 경로")
    args = parser.parse_args(argv)

    fetch_urls = import_module(f"scripts.{kind}.logic.list_scraper").fetch_urls
    logger = get_logger(__name__, scraper_type=kind)
    urls_found = await fetch_urls(getattr(args, "start_url", ""), args.max_pages)

    if not urls_found:
        logger.warning("추출된 URL이 없습니다.")
        return 0
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
        logger.info(f"'{output_dir}' 폴더를 생성했습니다.")
    with open(args.output, "w", encoding="utf-8") as f:
        for item in urls_found:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    logger.info(f"총 {len(urls_found)}개의 URL을 '{args.output}' 파일에 저장했습니다.")
    return 0


async def check_url_validity(kind: str, url: str) -> bool:
    """URL 에 접속해 기대하는 요소가 뜨는지 확인한다. 성공 True, 실패 False."""
    from playwright.async_api import TimeoutError as PlaywrightTimeout
    from playwright.async_api import async_playwright

    spec = CHECK_URL_KINDS[kind]
    logger = get_logger(__name__, scraper_type=kind)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until=spec.wait_until, timeout=spec.goto_timeout_ms)
            if spec.wait_loadmask:
                await page.wait_for_selector(".loadmask-msg", state="visible", timeout=10000)
                await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=30000)
            await page.wait_for_selector(spec.selector, state="visible", timeout=spec.selector_timeout_ms)
            logger.info(f"URL 유효성 검증 성공: {url}")
            return True
        except PlaywrightTimeout:
            logger.error(f"URL 유효성 검증 실패: 지정된 시간 내에 {spec.label} 콘텐츠가 뜨지 않았습니다. URL: {url}")
            return False
        except Exception as e:
            logger.error(f"URL 검증 중 알 수 없는 에러 발생: {e}", exc_info=True)
            return False
        finally:
            await browser.close()


async def run_check_url(kind: str, argv: list[str]) -> int:
    label = CHECK_URL_KINDS[kind].label
    parser = argparse.ArgumentParser(
        prog=f"manual.py check-url:{kind}", description=f"{label} 페이지 URL 이 정상으로 열리는지 확인한다.")
    parser.add_argument("url", help=f"검증할 {label} 페이지의 전체 URL")
    args = parser.parse_args(argv)
    # 쉘 스크립트가 판단할 수 있도록 종료 코드로 알린다(성공 0, 실패 1).
    return 0 if await check_url_validity(kind, args.url) else 1


ACTIONS = {
    "scrape": (SCRAPE_KINDS, run_scrape),
    "list": (LIST_KINDS, run_list),
    "check-url": (CHECK_URL_KINDS, run_check_url),
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] not in ACTIONS or argv[1] not in ACTIONS[argv[0]][0]:
        choices = ", ".join(f"{a}:{k}" for a, (kinds, _) in ACTIONS.items() for k in kinds)
        print(f"사용법: python3 manual.py <{choices}> [인자...]", file=sys.stderr)
        return 2
    runner = ACTIONS[argv[0]][1]
    return asyncio.run(runner(argv[1], argv[2:]))


if __name__ == "__main__":
    sys.exit(main())
