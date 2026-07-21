"""
law list_scraper — Playwright DOM 직접 파싱 방식

DRF open API(target=law)는 현행 버전만 반환하고 시행예정 버전을 포함하지 않는다.
law.go.kr 목록 페이지를 Playwright로 렌더링하면 현행 + 시행예정 모두 표시된다.
AJAX 파라미터를 재현하는 대신, 브라우저가 렌더링한 DOM을 직접 파싱하고
pageSearch() JS 함수로 페이지 이동한다.

onclick 패턴: lsReturnSearch('법령명','efYd','타입','lsiSeq','0')
  타입코드 '2' = 시행예정, '3' = 현행
"""

import asyncio
import os
import re
import sys
from datetime import date

from playwright.async_api import async_playwright

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='law')

# 수집 대상 소관부처: (cptOfiCd, 부처명)
ORG_CODES = [
    ("1492000", "고용노동부"),
    ("1790365", "개인정보보호위원회"),
]

LIST_URL_BASE = "https://www.law.go.kr/lsAstSc.do?menuId=391&subMenuId=397&tabMenuId=437"

# onclick 파싱: lsReturnSearch('법령명','efYd','타입코드','lsiSeq', ...)
_ONCLICK_RE = re.compile(
    r"lsReturnSearch\('([^']+)'\s*,\s*'(\d{8})'\s*,\s*'(\d+)'\s*,\s*'(\d+)'"
)
# "총 N건(현재/전체)" 패턴
_TOTAL_PAGES_RE = re.compile(r"총\s*[\d,]+건\s*\(\s*\d+\s*/\s*(\d+)\s*\)")


def _parse_total_pages(text: str) -> int:
    m = _TOTAL_PAGES_RE.search(text)
    return int(m.group(1)) if m else 1


async def _extract_page_items(page, dept_name: str, today: str, seen: set) -> list:
    """현재 렌더링된 페이지에서 법령 항목을 추출합니다."""
    onclicks = await page.evaluate('''() => {
        const result = [];
        document.querySelectorAll("table tbody tr").forEach(tr => {
            const a = tr.querySelector("a[onclick]");
            if (a) result.push(a.getAttribute("onclick") || "");
        });
        return result;
    }''')

    items = []
    for onclick in onclicks:
        m = _ONCLICK_RE.search(onclick)
        if not m:
            continue

        law_name, efy, type_code, lsi_seq = m.groups()
        law_name = law_name.strip()
        if not lsi_seq or lsi_seq in seen:
            continue
        seen.add(lsi_seq)

        is_upcoming = efy > today
        safe_name = re.sub(r'[\\/*?:"<>|]', "", law_name).strip() or f"law_{lsi_seq}"

        items.append({
            "name": safe_name,
            "url": f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={lsi_seq}&efYd={efy}",
            "effective": efy,
            "dept_name": dept_name,
            "law_id": law_name,   # 같은 법령의 버전 추적용 (법령명 = 그룹 키)
            "is_upcoming": is_upcoming,
        })

    return items


async def _fetch_org_all_pages(page, org_code: str, dept_name: str,
                               today: str, max_pages_arg: int | None) -> list:
    """한 부처의 전체 목록을 페이지별로 수집합니다."""
    main_url = f"{LIST_URL_BASE}&cptOfiCd={org_code}"
    await page.goto(main_url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(1500)

    # 총 페이지 수 파싱
    total_text = await page.evaluate('''() => {
        const el = document.querySelector(".search_list_total, .tit2, #lawListCnt");
        return el ? el.innerText : document.body.innerText.substring(0, 500);
    }''')
    total_pages = _parse_total_pages(total_text)
    logger.info(f"[{dept_name}] 총 {total_pages}페이지 (raw: {total_text[:50].strip()})")

    if max_pages_arg:
        total_pages = min(total_pages, max_pages_arg)

    seen: set = set()
    # 첫 페이지 추출
    all_items = await _extract_page_items(page, dept_name, today, seen)

    # 이후 페이지
    for page_idx in range(2, total_pages + 1):
        await page.evaluate(f"pageSearch('lsListDiv', '{page_idx}')")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(500)

        items = await _extract_page_items(page, dept_name, today, seen)
        all_items.extend(items)

        if page_idx % 5 == 0:
            logger.info(f"[{dept_name}] {page_idx}/{total_pages}페이지, 누적 {len(all_items)}건")

    return all_items


async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """
    법령 목록 페이지를 Playwright로 순회하며 URL 목록을 반환합니다.

    - ORG_CODES에 정의된 각 부처의 법령 수집 (현행 + 시행예정)
    - pageSearch() JS 호출로 페이지 이동, DOM에서 onclick 파싱

    Returns:
        list: [{"name", "url", "effective", "dept_name", "law_id", "is_upcoming"}, ...]
    """
    logger.info(f"법령 목록 수집 시작 (참조 URL: {start_url})")
    today = date.today().strftime("%Y%m%d")

    urls_found = []
    seen_urls: set = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        for org_code, dept_name in ORG_CODES:
            logger.info(f"[{dept_name}] org={org_code} 수집 시작")
            try:
                items = await _fetch_org_all_pages(
                    page, org_code, dept_name, today, max_pages_arg
                )
                for item in items:
                    url = item["url"]
                    if url not in seen_urls:
                        seen_urls.add(url)
                        urls_found.append(item)
                upcoming = sum(1 for it in items if it["is_upcoming"])
                logger.info(f"[{dept_name}] {len(items)}건 수집 (시행예정 {upcoming}건)")
            except Exception as e:
                logger.error(f"[{dept_name}] 수집 실패: {e}")

        await browser.close()

    logger.info(f"✅ 법령 URL 수집 완료: {len(urls_found)}건 (현행+시행예정)")
    return urls_found
