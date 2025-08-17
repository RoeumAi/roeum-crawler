import asyncio
import argparse
import sys
import os

# 실행 파일의 위치를 기준으로 프로젝트 루트 경로를 sys.path에 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

# 분리된 모듈에서 필요한 함수와 로거를 임포트
from scripts.law.scraper import scrape_and_save
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__)

async def main():
    parser = argparse.ArgumentParser(description="국가법령정보센터 법령 페이지를 스크레이핑하여 파일을 저장합니다.")
    parser.add_argument("url", help="스크레이핑할 법령 페이지의 전체 URL")
    parser.add_argument("-d", "--dept", required=True, help="데이터를 저장할 하위 폴더 이름 (보통 부처 코드)")
    parser.add_argument("-o", "--output", required=True, help="출력 파일의 기본 이름 (예: gasa-law)")
    args = parser.parse_args()

    output_dir = os.path.join(project_root, 'data', 'raw', args.dept)

    logger.info(f"상세 페이지 스크레이퍼 실행: {args.url}")
    await scrape_and_save(args.url, output_dir, args.output)
    logger.info(f"상세 페이지 스크레이퍼 완료: {args.output}")

if __name__ == "__main__":
    asyncio.run(main())
