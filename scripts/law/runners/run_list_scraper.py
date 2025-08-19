import asyncio
import json
import argparse
import sys
import os

# 1. 실행 파일의 위치를 기준으로 프로젝트 루트 경로를 sys.path에 추가
# 현재 파일(.../scripts)의 부모 폴더(...)가 프로젝트 루트
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# 2. 분리된 모듈에서 필요한 함수와 로거를 임포트
from scripts.law.logic.list_scraper import fetch_law_urls
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__)

async def main():
    # 3. 기존 list_scraper.py의 if __name__ == "__main__" 블록에 있던
    #    argparse 로직을 이곳으로 이동
    parser = argparse.ArgumentParser(description="법령 목록 페이지에서 상세 법령 URL들을 추출합니다.")
    parser.add_argument("start_url", help="크롤링을 시작할 법령 목록 페이지의 URL")
    parser.add_argument("-p", "--max_pages", type=int, default=None, help="크롤링할 최대 페이지 수 (없으면 전체)")
    parser.add_argument("-o", "--output", required=True, help="결과를 저장할 JSONL 파일의 전체 경로 (예: data/raw/urls.jsonl)")
    args = parser.parse_args()

    # 4. 임포트한 함수를 호출하여 실행하고 결과를 받음
    urls_found = await fetch_law_urls(args.start_url, args.max_pages)

    if urls_found:
        # 실행 파일은 루트에서 실행되므로, 루트에 urls.jsonl 생성
        output_filename = args.output
        output_dir = os.path.dirname(output_filename)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            logger.info(f"'{output_dir}' 폴더를 생성했습니다.")

        with open(output_filename, 'w', encoding='utf-8') as f:
            for item in urls_found:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        logger.info(f"총 {len(urls_found)}개의 URL을 '{output_filename}' 파일에 저장했습니다.")
    else:
        logger.warning("추출된 URL이 없습니다.")

if __name__ == "__main__":
    asyncio.run(main())
