"""
특정 법령명으로 law.go.kr 검색 → 현행 lsiSeq 추출 → 조문 크롤링 → MongoDB 저장

대상: 고용노동부/개인정보보호위원회 외 부처 소관이지만 DB에 필요한 6개 법령
"""

import asyncio
import os
import re
import sys
from datetime import date

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from playwright.async_api import async_playwright
from scripts.law.logic.scraper import scrape_and_save
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='law')

# ── 크롤링 대상 6개 법령 ──
TARGET_LAWS = [
    ("민사소송법",                          "법무부"),
    ("주택임대차보호법",                      "법무부"),
    ("부정경쟁방지 및 영업비밀보호에 관한 법률",    "특허청"),
    ("재난 및 안전관리 기본법",                 "행정안전부"),
    ("지방교육자치에 관한 법률",                 "교육부"),
    ("한부모가족지원법",                       "여성가족부"),
]

LIST_URL_BASE = "https://www.law.go.kr/lsAstSc.do?menuId=391&subMenuId=397&tabMenuId=437"

_ONCLICK_RE = re.compile(
    r"lsReturnSearch\('([^']+)'\s*,\s*'(\d{8})'\s*,\s*'(\d+)'\s*,\s*'(\d+)'"
)


async def find_current_lsi_seq(page, law_name: str, today: str) -> dict | None:
    """
    법령명으로 law.go.kr를 검색해 현행(is_upcoming=False) 버전의 lsiSeq를 반환.
    없으면 가장 최신 버전을 반환.
    """
    search_url = f"{LIST_URL_BASE}&query={law_name}"
    logger.info(f"  검색 URL: {search_url}")

    await page.goto(search_url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(1500)

    onclicks = await page.evaluate('''() => {
        const result = [];
        document.querySelectorAll("table tbody tr").forEach(tr => {
            const a = tr.querySelector("a[onclick]");
            if (a) result.push(a.getAttribute("onclick") || "");
        });
        return result;
    }''')

    candidates = []
    for onclick in onclicks:
        m = _ONCLICK_RE.search(onclick)
        if not m:
            continue
        name, efy, type_code, lsi_seq = m.groups()
        candidates.append({
            "law_name": name.strip(),
            "efy": efy,
            "lsi_seq": lsi_seq,
            "is_upcoming": efy > today,
        })

    if not candidates:
        logger.warning(f"  [{law_name}] 검색 결과 없음")
        return None

    # 검색어와 법령명이 가장 가까운 것 우선, 그 중 현행 버전 우선
    def score(c):
        # 정확히 일치하면 0점(최우선), 포함이면 1점, 나머지 2점
        stripped = re.sub(r'\s', '', law_name)
        cname    = re.sub(r'\s', '', c["law_name"])
        match_score = 0 if cname == stripped else (1 if stripped in cname or cname in stripped else 2)
        upcoming_score = 1 if c["is_upcoming"] else 0  # 현행 우선
        return (match_score, upcoming_score)

    candidates.sort(key=score)
    best = candidates[0]
    logger.info(f"  [{law_name}] 선택: {best['law_name']} efy={best['efy']} lsiSeq={best['lsi_seq']} 시행예정={best['is_upcoming']}")
    return best


async def main():
    today = date.today().strftime("%Y%m%d")
    output_dir = os.path.join(project_root, "data", "output", "law", "cherry_pick")
    os.makedirs(output_dir, exist_ok=True)

    results = {"success": [], "failed": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        for law_name, dept_name in TARGET_LAWS:
            logger.info(f"\n{'='*50}")
            logger.info(f"[{dept_name}] {law_name} 검색 중...")

            hit = await find_current_lsi_seq(page, law_name, today)
            if not hit:
                results["failed"].append(f"{law_name} (검색 결과 없음)")
                continue

            url = f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={hit['lsi_seq']}&efYd={hit['efy']}"
            safe_name = re.sub(r'[\\/*?:"<>| ]', "_", law_name)

            url_dict = {
                "url":         url,
                "dept_name":   dept_name,
                "law_id":      hit["law_name"],
                "is_upcoming": hit["is_upcoming"],
            }

            logger.info(f"  크롤링 시작: {url}")
            try:
                result = await scrape_and_save(
                    url_dict,
                    output_dir,
                    safe_name,
                    dept_code=None,
                    save_to_db=True,
                    save_jsonl=True,
                )
                if result and result.get("status") == "success":
                    logger.info(f"  ✅ 저장 완료: doc_id={result['doc_id']}")
                    results["success"].append(f"{law_name} (doc_id={result['doc_id']})")
                else:
                    logger.error(f"  ❌ 저장 실패")
                    results["failed"].append(f"{law_name} (스크래핑 실패)")
            except Exception as e:
                logger.error(f"  ❌ 오류: {e}")
                results["failed"].append(f"{law_name} ({e})")

        await browser.close()

    logger.info(f"\n{'='*50}")
    logger.info(f"✅ 성공 {len(results['success'])}건:")
    for r in results["success"]:
        logger.info(f"  {r}")
    if results["failed"]:
        logger.info(f"❌ 실패 {len(results['failed'])}건:")
        for r in results["failed"]:
            logger.info(f"  {r}")


if __name__ == "__main__":
    asyncio.run(main())
