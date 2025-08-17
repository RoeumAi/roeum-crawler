import asyncio
from playwright.async_api import async_playwright
import json
import re
import argparse
import os

def build_detail_url(onclick_attr: str):
    """onclick 속성값에서 lsiSeq, efYd 등을 추출하여 상세 페이지 URL을 생성합니다."""
    m = re.search(r"lsReturnSearch\((.*?)\)", onclick_attr or "")
    if not m:
        return None

    args = re.findall(r"'([^']*)'", m.group(1))
    efYd = next((x for x in args if re.fullmatch(r"\d{8}", x)), None)

    nums = [x for x in args if re.fullmatch(r"\d{5,}", x)]
    if efYd in nums:
        nums.remove(efYd)

    lsiSeq = nums[-1] if nums else None

    if not lsiSeq:
        return None

    url = f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={lsiSeq}"
    if efYd:
        url += f"&efYd={efYd}"

    return url

async def main(start_url: str, max_pages_arg: int | None):
    """법령 목록 페이지를 순회하며 상세 페이지 URL을 추출합니다."""

    urls_found = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            print(f"목록 페이지로 이동 중: {start_url}")
            await page.goto(start_url, wait_until='domcontentloaded', timeout=60000)

            # ⭐️ [핵심 수정 1] 전체 페이지 수 자동 감지
            print("로딩 마스크 대기 중...")
            try:
                await page.wait_for_selector(".loadmask-msg", state="visible", timeout=10000)
                await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=30000)
            except Exception:
                print("⚠️ 로딩 마스크가 감지되지 않았습니다. 계속 진행합니다.")

            total_pages = 1
            try:
                # 페이지네이션 요소에서 "(현재 / 전체)" 텍스트 추출
                pagination_text = await page.inner_text("span.page")
                match = re.search(r'\(\s*\d+\s*/\s*(\d+)\s*\)', pagination_text)
                if match:
                    total_pages = int(match.group(1))
                    print(f"✅ 총 {total_pages} 페이지를 확인했습니다.")
                else:
                    print("⚠️ 페이지네이션 정보를 찾을 수 없어 1페이지만 크롤링합니다.")
            except Exception:
                print("⚠️ 페이지네이션 요소를 찾을 수 없어 1페이지만 크롤링합니다.")

            # ⭐️ [핵심 수정 2] 크롤링할 최종 페이지 수 결정
            pages_to_crawl = total_pages
            if max_pages_arg is not None and max_pages_arg < total_pages:
                pages_to_crawl = max_pages_arg
                print(f"➡️ 사용자 설정에 따라 최대 {pages_to_crawl} 페이지만 크롤링합니다.")

            # --- 페이지 순회 시작 ---
            for page_num in range(1, pages_to_crawl + 1):
                print(f"\n--- {page_num} / {pages_to_crawl} 페이지 처리 중 ---")

                if page_num > 1:
                    print(f"{page_num} 페이지로 이동합니다...")
                    await page.evaluate(f"pageSearch('lsListDiv','{page_num}')")
                    print("로딩 마스크 대기 중...")
                    try:
                        await page.wait_for_selector(".loadmask-msg", state="visible", timeout=10000)
                        await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=30000)
                    except Exception:
                        print("⚠️ 로딩 마스크가 감지되지 않았습니다. 계속 진행합니다.")
                    await page.wait_for_timeout(1000)

                law_links = await page.query_selector_all("#resultTableDiv a[onclick*='lsReturnSearch']")

                if not law_links:
                    print("더 이상 법령 목록이 없습니다.")
                    break

                for link in law_links:
                    onclick = await link.get_attribute("onclick")
                    law_name = await link.inner_text()
                    detail_url = build_detail_url(onclick)

                    if detail_url and law_name:
                        safe_name = re.sub(r'[\\/*?:"<>|]', "", law_name).strip()
                        urls_found.append({"name": safe_name, "url": detail_url})
                        print(f"  - 발견: {law_name}")

        except Exception as e:
            print(f"❌ 목록 스크레이핑 중 에러 발생: {e}")
        finally:
            await browser.close()

    if urls_found:
        output_filename = "urls.jsonl"
        with open(output_filename, 'w', encoding='utf-8') as f:
            for item in urls_found:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        print(f"\n✅ 총 {len(urls_found)}개의 URL을 '{output_filename}' 파일에 저장했습니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="법령 목록 페이지에서 상세 법령 URL들을 추출합니다.")
    parser.add_argument("start_url", help="크롤링을 시작할 법령 목록 페이지의 URL")
    # ⭐️ [핵심 수정 3] 기본값을 None으로 변경하여 전체 크롤링과 부분 크롤링 구분
    parser.add_argument("-p", "--max_pages", type=int, default=None, help="크롤링할 최대 페이지 수 (지정하지 않으면 감지된 전체 페이지를 크롤링)")

    args = parser.parse_args()

    asyncio.run(main(args.start_url, args.max_pages))