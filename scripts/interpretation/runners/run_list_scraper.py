import asyncio
import json
import argparse
import sys
import os

# 프로젝트 루트 경로 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# 분리된 모듈 임포트
from scripts.interpretation.logic.list_scraper import fetch_urls
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')

async def main():
    """스크립트 실행을 위한 메인 함수."""
    parser = argparse.ArgumentParser(description="행정해석 목록 페이지에서 상세 URL들을 추출합니다.")
    parser.add_argument("start_url", help="크롤링을 시작할 목록 페이지의 URL")

    # --- [수정] 아래 한 줄을 추가하여 --dept_code 인자를 받도록 설정 ---
    parser.add_argument("--dept_code", required=True, help="필터링할 부처 코드")

    parser.add_argument("-o", "--output", required=True, help="추출된 URL을 저장할 파일 경로")
    parser.add_argument("--max_pages", type=int, default=None, help="크롤링할 최대 페이지 수")
    args = parser.parse_args()

    # dept_code 인자를 fetch_urls 함수에 전달
    urls = await fetch_urls(args.start_url, args.dept_code, args.max_pages)

    if urls:
        output_dir = os.path.dirname(args.output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        with open(args.output, 'w', encoding='utf-8') as f:
            for item in urls:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        logger.info(f"총 {len(urls)}개의 URL을 '{args.output}' 파일에 저장했습니다.")
    else:
        logger.warning("추출된 URL이 없습니다.")


if __name__ == "__main__":
    asyncio.run(main())

