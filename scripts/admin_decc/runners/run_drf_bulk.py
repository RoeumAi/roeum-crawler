"""
admin_decc DRF 기반 일괄 수집 runner
- DRF API로 고용노동부 관련 행정심판재결례 전체 스캔
- MongoDB에 없는 신규 건만 크롤링하여 저장
"""
import asyncio
import os
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.admin_decc.logic.list_scraper import fetch_urls
from scripts.admin_decc.logic.scraper import scrape_and_save

logger = get_logger(__name__, scraper_type='admin_decc')

MAX_CONCURRENT = 3


async def run():
    start = datetime.now()
    logger.info('=== admin_decc DRF 일괄 수집 시작 ===')

    # 1단계: URL 목록 수집 (신규 건만)
    url_items = await fetch_urls()
    total = len(url_items)

    if total == 0:
        logger.info('신규 수집 대상 없음 — 종료')
        return

    logger.info(f'신규 수집 대상: {total}건 — 크롤링 시작')

    # 2단계: 상세 페이지 크롤링 (동시 3건)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    success = 0
    failed = 0
    failed_list = []

    async def _crawl(item):
        nonlocal success, failed
        async with semaphore:
            result = await scrape_and_save(
                url=item,
                output_dir='',
                output_name=item.get('name', ''),
                process_chunks=True,
                save_to_db=True,
                save_jsonl=False,
            )
            if result and result.get('status') == 'success':
                return True
            else:
                failed_list.append(item.get('url', ''))
                return False

    tasks = [_crawl(item) for item in url_items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if r is True:
            success += 1
        else:
            failed += 1

    elapsed = (datetime.now() - start).seconds
    logger.info(
        f'=== 완료 === 성공: {success}건 / 실패: {failed}건 / 소요: {elapsed}초'
    )
    if failed_list:
        logger.warning(f'실패 URL 목록:')
        for u in failed_list[:20]:
            logger.warning(f'  {u}')


if __name__ == '__main__':
    asyncio.run(run())
