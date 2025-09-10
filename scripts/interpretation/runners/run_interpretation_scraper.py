import asyncio
import argparse
import os
import sys

# 프로젝트 루트 경로 설정
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# 로직 모듈과 로거 임포트
from scripts.interpretation.logic.interpretation_scraper import scrape_interpretation_data
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')

async def main():
    parser = argparse.ArgumentParser(description="행정해석 데이터를 통합적으로 스크레이핑합니다.")
    parser.add_argument("start_url", help="크롤링을 시작할 기본 목록 페이지 URL")
    parser.add_argument("--dept_code", required=True, help="필터링할 부처 코드")
    parser.add_argument("-o", "--output_dir", required=True, help="결과를 저장할 폴더 경로")
    parser.add_argument("--max_pages", type=int, default=None, help="크롤링할 최대 페이지 수")
    args = parser.parse_args()

    logger.info("통합 스크레이퍼 실행...")
    await scrape_interpretation_data(args.start_url, args.dept_code, args.output_dir, args.max_pages)
    logger.info("통합 스크레이퍼 완료.")

if __name__ == "__main__":
    asyncio.run(main())

