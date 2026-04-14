import asyncio
import argparse
import sys
import os

# 프로젝트 루트 경로 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.judgment.logic.scraper import scrape_and_save
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='judgment')


async def main():
    parser = argparse.ArgumentParser(description="주요판정사례 상세 페이지를 스크레이핑합니다.")
    parser.add_argument("url", help="스크레이핑할 상세 페이지 URL")
    parser.add_argument("-o", "--output", required=True, help="출력 파일의 기본 이름 (확장자 제외)")
    parser.add_argument("--no-db", action="store_true", help="MongoDB 저장하지 않음")
    args = parser.parse_args()

    output_dir = os.path.join(project_root, 'data', 'raw', 'judgment')
    save_to_db = not args.no_db

    logger.info(f"상세 페이지 스크레이퍼 실행: {args.url}")
    await scrape_and_save(
        args.url,
        output_dir,
        args.output,
        save_to_db=save_to_db,
        save_jsonl=True
    )
    logger.info(f"상세 페이지 스크레이퍼 완료: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
