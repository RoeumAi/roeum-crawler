"""조정사건례 목록 스크래퍼 (nlrc.go.kr, 분류 코드 66). 공통 수집은 scripts/utils/nlrc_list.py.

사이트 주소의 철자(mediatioin)는 원래 그렇다."""
import asyncio
import os
import sys
from typing import Optional

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.utils.nlrc_list import fetch_nlrc_list

logger = get_logger(__name__, scraper_type='mediation_case')

CODE = "66"
DETAIL_BASE_URL = "https://nlrc.go.kr/nlrc/mainCase/mediatioin/detail.do"
REFERER = "https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do"


async def fetch_urls(start_url: str, max_pages_arg: Optional[int] = None) -> list[dict]:
    """조정사건례 상세 URL 목록: [{"name", "url", "jgmt_sn", "jgmt_dcsn_se_cd"}, ...]"""
    logger.info(f"조정사건례 목록 수집 시작 (참조 URL: {start_url})")
    return await fetch_nlrc_list(
        code=CODE, detail_base_url=DETAIL_BASE_URL, referer=REFERER, name_prefix="mediation",
        logger=logger, max_pages=max_pages_arg,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="조정사건례 URL 수집기")
    parser.add_argument("--max_pages", type=int, default=None, help="최대 페이지 수")
    args = parser.parse_args()

    urls = asyncio.run(fetch_urls(REFERER, args.max_pages))
    print(f"\n수집된 URL: {len(urls)}개")
    for i, item in enumerate(urls[:5], 1):
        print(f"{i}. {item['name']}: {item['url']}")
