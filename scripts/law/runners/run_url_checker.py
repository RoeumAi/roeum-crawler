import asyncio
import argparse
import sys
import os

# 실행 파일의 위치를 기준으로 프로젝트 루트 경로를 sys.path에 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# 분리된 모듈에서 필요한 함수를 임포트
from scripts.law.logic.url_checker import check_url_validity
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='law')

async def main():
    # argparse 로직을 이곳으로 이동
    parser = argparse.ArgumentParser(description="법령 목록 URL의 유효성을 검증합니다.")
    parser.add_argument("url", help="검증할 법령 목록 페이지의 전체 URL")
    args = parser.parse_args()

    # 임포트한 함수를 호출하여 실행하고 결과를 받음
    is_valid = await check_url_validity(args.url)

    # 쉘 스크립트가 판단할 수 있도록 종료 코드를 반환
    if is_valid:
        exit(0) # 성공
    else:
        exit(1) # 실패

if __name__ == "__main__":
    asyncio.run(main())
