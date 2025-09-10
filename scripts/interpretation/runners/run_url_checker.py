import asyncio
import argparse
import sys
import os

# 프로젝트 루트 경로 설정
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# 분리된 로직 모듈에서 필요한 함수와 로거를 임포트
from scripts.interpretation.logic.url_checker import check_url_validity
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')

async def main():
    """스크립트 실행을 위한 메인 함수"""
    parser = argparse.ArgumentParser(description="URL이 유효한지, 콘텐츠가 정상적으로 로드되는지 확인합니다.")
    parser.add_argument("url", help="검증할 페이지의 URL")
    args = parser.parse_args()

    # 임포트한 함수를 호출하여 실행하고 결과를 받음
    is_valid = await check_url_validity(args.url)

    if is_valid:
        sys.exit(0)  # 성공 시 종료 코드 0
    else:
        sys.exit(1)  # 실패 시 종료 코드 1 (쉘 스크립트에서 감지)

if __name__ == "__main__":
    asyncio.run(main())

