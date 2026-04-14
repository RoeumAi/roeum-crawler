import asyncio
import json
import argparse
import sys
import os

# 프로젝트 루트 경로 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.judgment.logic.list_scraper import fetch_urls
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='judgment')


async def main():
    parser = argparse.ArgumentParser(description="주요판정사례 목록 페이지에서 URL을 추출합니다.")
    parser.add_argument("-p", "--max_pages", type=int, default=None, help="크롤링할 최대 페이지 수")
    parser.add_argument("-o", "--output", required=True, help="결과를 저장할 JSONL 파일 경로")
    args = parser.parse_args()

    urls_found = await fetch_urls("https://nlrc.go.kr/nlrc/mainCase/judgment/index.do", args.max_pages)

    if urls_found:
        output_dir = os.path.dirname(args.output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            logger.info(f"'{output_dir}' 폴더를 생성했습니다.")

        with open(args.output, 'w', encoding='utf-8') as f:
            for item in urls_found:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        logger.info(f"총 {len(urls_found)}개의 URL을 '{args.output}' 파일에 저장했습니다.")
    else:
        logger.warning("추출된 URL이 없습니다.")


if __name__ == "__main__":
    asyncio.run(main())
