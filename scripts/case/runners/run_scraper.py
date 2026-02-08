import asyncio
import argparse
import sys
import os

# 실행 파일의 위치를 기준으로 프로젝트 루트 경로를 sys.path에 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# 분리된 모듈에서 필요한 함수와 로거를 임포트
from scripts.case.logic.scraper import scrape_and_save
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='case')

async def main():
    """
    커맨드 라인 인자를 받아 스크레이핑 로직을 실행하는 메인 함수.
    """
    parser = argparse.ArgumentParser(description="국가법령정보센터 판례 페이지를 스크레이핑하여 파일을 저장합니다.")
    parser.add_argument("url", help="스크레이핑할 판례 페이지의 전체 URL")
    parser.add_argument("-d", "--dept", required=True, help="데이터를 저장할 하위 폴더 이름 (부처 코드)")
    parser.add_argument("-o", "--output", required=True, help="출력 파일의 기본 이름 (확장자 제외)")
    parser.add_argument("--no-db", action="store_true", help="MongoDB 저장하지 않음 (기본값: 저장함)")
    args = parser.parse_args()

    # 최종 데이터가 저장될 경로를 동적으로 생성
    output_dir = os.path.join(project_root, 'data', 'raw', 'case', args.dept)
    save_to_db = not args.no_db

    logger.info(f"상세 페이지 스크레이퍼 실행: {args.url}")
    await scrape_and_save(
        args.url, 
        output_dir, 
        args.output, 
        dept_code=args.dept,
        process_chunks=True, 
        save_to_db=save_to_db
    )
    logger.info(f"상세 페이지 스크레이퍼 완료: {args.output}")

if __name__ == "__main__":
    asyncio.run(main())

