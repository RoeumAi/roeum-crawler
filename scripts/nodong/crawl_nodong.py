"""
nodong.kr 행정해석 크롤러 (최초 full insert 전용)

사용법:
  python3 scripts/nodong/crawl_nodong.py               # 전체 25페이지
  python3 scripts/nodong/crawl_nodong.py --pages 1     # 테스트: 1페이지만
  python3 scripts/nodong/crawl_nodong.py --concurrent 5  # 동시 처리 수 조정
"""
import asyncio
import argparse
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(project_root)

from scripts.nodong.list_scraper import fetch_all_urls
from scripts.nodong.scraper import scrape_and_save
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')


def run(max_pages: int | None, concurrent: int):
    start_time = datetime.now()
    print("=" * 60)
    print("🚀 nodong.kr 행정해석 크롤링 시작")
    print(f"   최대 페이지: {max_pages or '전체(25)'}")
    print(f"   동시 처리 수: {concurrent}")
    print(f"   시작: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: URL 수집
    print("\n📍 Step 1: URL 목록 수집...")
    url_items = fetch_all_urls(max_pages)
    print(f"✅ Step 1 완료: {len(url_items)}개 URL 발견")

    if not url_items:
        print("⚠️  수집된 URL이 없습니다. 종료합니다.")
        return

    # Step 2: 상세 페이지 크롤링 + MongoDB 저장
    print(f"\n📍 Step 2: 상세 페이지 크롤링 + MongoDB 저장 (동시 {concurrent}개)...")
    success_count = 0
    failed_count = 0

    with ThreadPoolExecutor(max_workers=concurrent) as executor:
        futures = {executor.submit(scrape_and_save, item): item for item in url_items}

        for i, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            try:
                result = future.result()
                if result["status"] == "success":
                    success_count += 1
                    logger.info(f"[{i}/{len(url_items)}] ✅ {result['doc_id']}")
                else:
                    failed_count += 1
                    logger.warning(f"[{i}/{len(url_items)}] ❌ 실패: {item['url']}")
            except Exception as e:
                failed_count += 1
                logger.error(f"[{i}/{len(url_items)}] ❌ 예외: {e}")

            if i % 50 == 0:
                print(f"   진행: {i}/{len(url_items)} (성공 {success_count}, 실패 {failed_count})")

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds() / 60

    print("\n" + "=" * 60)
    print("📊 크롤링 완료")
    print("=" * 60)
    print(f"   발견 URL:  {len(url_items)}개")
    print(f"   성공:      {success_count}개")
    print(f"   실패:      {failed_count}개")
    print(f"   소요 시간: {elapsed:.1f}분")
    print(f"   종료:      {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="nodong.kr 행정해석 크롤러")
    parser.add_argument("--pages", type=int, default=None, help="최대 페이지 수 (기본: 전체 25페이지)")
    parser.add_argument("--concurrent", type=int, default=3, help="동시 처리 수 (기본: 3)")
    args = parser.parse_args()

    run(max_pages=args.pages, concurrent=args.concurrent)
