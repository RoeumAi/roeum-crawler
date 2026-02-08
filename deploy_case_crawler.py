#!/usr/bin/env python3
"""
Case 판례 Prefect 배포 설정

이 스크립트는 Prefect 플로우를 정기적으로 실행하도록 배포합니다.
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# 프로젝트 루트 추가
project_root = str(Path(__file__).parent)
sys.path.insert(0, project_root)

from prefect import flow
from prefect.schedules import CronSchedule
from scripts.core.flows.unified_scraper_flow import unified_scraper_flow


@flow(name="Case Crawler Flow", description="판례(Case) 정기 크롤링")
async def case_crawler_flow(max_pages=None, max_concurrent=3):
    """
    판례(Case) 크롤링 Prefect 플로우
    
    Args:
        max_pages: 최대 크롤링 페이지 (None = 모든 페이지)
        max_concurrent: 동시 처리 수
    """
    await unified_scraper_flow(
        scraper_type='case',
        max_pages=max_pages,
        max_concurrent=max_concurrent
    )


async def deploy_case_crawler():
    """Case 크롤러를 Prefect에 배포"""
    
    print("=" * 80)
    print("🚀 Case Crawler Prefect 배포 시작")
    print("=" * 80)
    
    # 배포 설정
    deployment = case_crawler_flow.to_deployment(
        name="case-hourly",
        description="매시간 정기적으로 판례 크롤링 수행",
        tags=["case", "production"],
        work_pool_name="default",
        # 매시간 0분에 실행
        schedule=CronSchedule(cron="0 * * * *"),
        parameters={
            "max_pages": None,  # 모든 페이지 크롤링
            "max_concurrent": 5
        }
    )
    
    print(f"\n📋 배포 정보:")
    print(f"  - 플로우명: {deployment.name}")
    print(f"  - 설명: {deployment.description}")
    print(f"  - 스케줄: 매시간 0분")
    print(f"  - 워크 풀: {deployment.work_pool_name}")
    print(f"  - 태그: {', '.join(deployment.tags)}")
    
    print("\n✅ Case Crawler 배포 완료!")
    print("\n📌 다음 단계:")
    print("   1. Prefect 서버 시작: prefect server start")
    print("   2. 워커 시작: prefect worker start --pool default")
    print("   3. 대시보드 접속: http://127.0.0.1:4200")
    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(deploy_case_crawler())
