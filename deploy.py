#!/usr/bin/env python3
"""
Prefect Deployment 자동 생성 및 관리

모든 scraper에 대해 Prefect deployment를 자동으로 생성합니다.

사용법:
    python3 deploy.py                   # 모든 scraper deployment 생성
    python3 deploy.py --scraper law     # law deployment만 생성
    python3 deploy.py --list            # 현재 배포된 deployment 목록
    python3 deploy.py --delete law      # law deployment 삭제
"""

import sys
import os
from pathlib import Path
from datetime import datetime
import argparse
import subprocess
import json

# 프로젝트 루트 추가
project_root = str(Path(__file__).parent)
sys.path.insert(0, project_root)

from scripts.core.config import get_scraper_config, get_scraper_list


def parse_args():
    """명령행 인자 파싱"""
    parser = argparse.ArgumentParser(
        description='Prefect Deployment 관리',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 모든 deployment 생성
  python3 deploy.py
  
  # 특정 deployment만 생성
  python3 deploy.py --scraper law
  
  # 현재 배포된 deployment 목록
  python3 deploy.py --list
  
  # deployment 삭제
  python3 deploy.py --delete law
        """
    )
    
    parser.add_argument(
        '--scraper',
        nargs='+',
        default=None,
        help='배포할 scraper'
    )
    
    parser.add_argument(
        '--list',
        action='store_true',
        help='현재 배포된 deployment 목록'
    )
    
    parser.add_argument(
        '--delete',
        nargs='+',
        default=None,
        help='삭제할 scraper deployment'
    )
    
    parser.add_argument(
        '--schedule',
        default='0 9 * * 1',  # 매주 월요일 09:00
        help='Cron 스케줄 (기본값: 매주 월요일 09:00)'
    )

    parser.add_argument(
        '--refresh',
        action='store_true',
        help='law/adrule 현재 시행 버전 일일 재계산 deployment 생성'
    )

    return parser.parse_args()


def run_command(cmd, description=""):
    """명령어 실행"""
    if description:
        print(f"\n📌 {description}")
        print(f"   명령어: {cmd}")
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"   ❌ 실패")
        if result.stderr:
            print(f"   오류: {result.stderr}")
        return False
    
    if result.stdout:
        print(f"   ✅ {result.stdout.strip()}")
    
    return True


def deploy_scraper(scraper_type, schedule):
    """단일 scraper deployment 생성"""
    config = get_scraper_config(scraper_type)
    
    deployment_name = f"{scraper_type}-scraper"
    
    # Python 스크립트로 deployment 생성
    deploy_script = f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from prefect import flow
from prefect.deployments import Deployment
from scripts.core.flows.unified_scraper_flow import unified_scraper_flow

deployment = Deployment.build_from_flow(
    flow=unified_scraper_flow,
    name="{deployment_name}",
    parameters={{
        "scraper_type": "{scraper_type}",
        "max_pages": None,
        "max_concurrent": 3
    }},
    schedule="{schedule}"
)

deployment.apply()
print(f"✅ {{deployment.name}} 배포 완료")
"""
    
    # 임시 파일에 스크립트 작성 및 실행
    temp_file = f"/tmp/deploy_{scraper_type}.py"
    with open(temp_file, 'w') as f:
        f.write(deploy_script)
    
    cmd = f"cd {project_root} && python3 {temp_file}"
    return run_command(cmd, f"{config.display_name} deployment 생성")


def deploy_refresh_flow(schedule='10 0 * * *'):
    """law/adrule 현재 시행 버전 일일 재계산 deployment 생성"""
    deploy_script = f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from prefect.deployments import Deployment
from scripts.core.flows.refresh_current_status_flow import refresh_current_status_flow

deployment = Deployment.build_from_flow(
    flow=refresh_current_status_flow,
    name="refresh-current-status",
    schedule="{schedule}"
)

deployment.apply()
print(f"✅ {{deployment.name}} 배포 완료")
"""
    temp_file = "/tmp/deploy_refresh_current_status.py"
    with open(temp_file, 'w') as f:
        f.write(deploy_script)

    cmd = f"cd {project_root} && python3 {temp_file}"
    return run_command(cmd, "law/adrule 현재 시행 버전 재계산 deployment 생성")


def list_deployments():
    """현재 배포된 deployment 목록"""
    cmd = "prefect deployment ls"
    print("\n📋 현재 배포된 Deployment 목록:")
    print("="*80)
    run_command(cmd)
    print("="*80)


def delete_deployment(scraper_type):
    """deployment 삭제"""
    config = get_scraper_config(scraper_type)
    deployment_name = f"{scraper_type}-scraper"
    
    cmd = f"prefect deployment delete '{deployment_name}'"
    description = f"{config.display_name} deployment 삭제"
    
    return run_command(cmd, description)


def main():
    """메인 함수"""
    args = parse_args()
    
    print("\n" + "="*80)
    print("🚀 Prefect Deployment 관리")
    print("="*80)
    
    # --list 옵션
    if args.list:
        list_deployments()
        return 0
    
    # --delete 옵션
    if args.delete:
        print(f"\n🗑️  다음 deployment를 삭제합니다: {', '.join(args.delete)}")
        for scraper_type in args.delete:
            delete_deployment(scraper_type)
        return 0

    # --refresh 옵션 (law/adrule 현재 시행 버전 일일 재계산)
    if args.refresh:
        print("\n🔍 Prefect 서버 상태 확인 중...")
        result = subprocess.run("prefect server health-check", shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print("\n⚠️  Prefect 서버가 실행 중이지 않습니다.")
            print("   다음 명령어로 서버를 시작하세요:")
            print("   prefect server start")
            print()
            return 1
        print("✅ Prefect 서버가 실행 중입니다.\n")
        success = deploy_refresh_flow()
        return 0 if success else 1

    # 배포할 scraper 결정
    if args.scraper is None:
        scrapers = get_scraper_list()
        print(f"\n📌 모든 scraper에 대해 deployment를 생성합니다")
    else:
        scrapers = args.scraper
        print(f"\n📌 다음 scraper에 대해 deployment를 생성합니다: {', '.join(scrapers)}")
    
    print(f"📅 스케줄: {args.schedule}")
    print("="*80)
    
    # Prefect 서버 실행 확인
    print("\n🔍 Prefect 서버 상태 확인 중...")
    result = subprocess.run("prefect server health-check", shell=True, capture_output=True, text=True)
    
    if result.returncode != 0:
        print("\n⚠️  Prefect 서버가 실행 중이지 않습니다.")
        print("   다음 명령어로 서버를 시작하세요:")
        print("   prefect server start")
        print()
        return 1
    
    print("✅ Prefect 서버가 실행 중입니다.\n")
    
    # Deployment 생성
    print("📦 Deployment 생성 중...\n")
    
    success_count = 0
    failed_count = 0
    
    for scraper_type in scrapers:
        try:
            if deploy_scraper(scraper_type, args.schedule):
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            print(f"   ❌ 오류: {e}")
            failed_count += 1
    
    # 최종 결과
    print("\n" + "="*80)
    print("✅ Deployment 생성 완료")
    print("="*80)
    print(f"   - 성공: {success_count}개")
    print(f"   - 실패: {failed_count}개")
    print()
    
    if success_count > 0:
        print("🎯 다음 단계:")
        print("   1. Prefect Worker 시작: prefect worker start --pool default")
        print("   2. Dashboard 접속: http://localhost:4200")
        print()
    
    return 0 if failed_count == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
