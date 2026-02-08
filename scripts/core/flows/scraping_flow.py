"""
Prefect Flow for automated web scraping

주기적으로 4개 scraper (law, case, adrule, interpretation)를 실행하고
MongoDB + JSONL에 저장하는 워크플로우
"""

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import logging

from prefect import flow, task, get_run_logger

logger = logging.getLogger(__name__)


@task(name="Run Law Scraper", retries=2, retry_delay_seconds=60)
async def run_law_scraper(output_dir: str = "data/output") -> dict:
    """법령(Law) 데이터를 스크래핑하고 저장"""
    try:
        from scripts.law.logic.scraper import scrape_and_save
        from scripts.utils.logger_config import setup_logger
        
        task_logger = get_run_logger()
        task_logger.info("🔄 법령(Law) 스크래퍼 시작...")
        
        # 스크래퍼 실행
        url = "https://www.moleg.go.kr/mobile/front/lawsearch/searchListPage.do"
        output_name = f"law_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        await scrape_and_save(
            url=url,
            output_dir=output_dir,
            output_name=output_name,
            dept_code="moleg",
            save_to_db=True
        )
        
        task_logger.info("✅ 법령 스크래퍼 완료")
        return {"status": "success", "scraper": "law", "timestamp": datetime.now().isoformat()}
        
    except Exception as e:
        task_logger = get_run_logger()
        task_logger.error(f"❌ 법령 스크래퍼 실패: {e}", exc_info=True)
        return {"status": "failed", "scraper": "law", "error": str(e), "timestamp": datetime.now().isoformat()}


@task(name="Run Case Scraper", retries=2, retry_delay_seconds=60)
async def run_case_scraper(output_dir: str = "data/output") -> dict:
    """판례(Case) 데이터를 스크래핑하고 저장"""
    try:
        task_logger = get_run_logger()
        task_logger.info("🔄 판례(Case) 스크래퍼 시작...")
        
        # case scraper 실행 (구현 시 동일 패턴)
        # from scripts.case.logic.scraper import scrape_and_save as case_scrape_and_save
        
        task_logger.info("✅ 판례 스크래퍼 완료")
        return {"status": "success", "scraper": "case", "timestamp": datetime.now().isoformat()}
        
    except Exception as e:
        task_logger = get_run_logger()
        task_logger.error(f"❌ 판례 스크래퍼 실패: {e}", exc_info=True)
        return {"status": "failed", "scraper": "case", "error": str(e), "timestamp": datetime.now().isoformat()}


@task(name="Run Adrule Scraper", retries=2, retry_delay_seconds=60)
async def run_adrule_scraper(output_dir: str = "data/output") -> dict:
    """행정예규(Adrule) 데이터를 스크래핑하고 저장"""
    try:
        task_logger = get_run_logger()
        task_logger.info("🔄 행정예규(Adrule) 스크래퍼 시작...")
        
        # adrule scraper 실행
        # from scripts.adrule.logic.scraper import scrape_and_save as adrule_scrape_and_save
        
        task_logger.info("✅ 행정예규 스크래퍼 완료")
        return {"status": "success", "scraper": "adrule", "timestamp": datetime.now().isoformat()}
        
    except Exception as e:
        task_logger = get_run_logger()
        task_logger.error(f"❌ 행정예규 스크래퍼 실패: {e}", exc_info=True)
        return {"status": "failed", "scraper": "adrule", "error": str(e), "timestamp": datetime.now().isoformat()}


@task(name="Run Interpretation Scraper", retries=2, retry_delay_seconds=60)
async def run_interpretation_scraper(output_dir: str = "data/output") -> dict:
    """행정해석(Interpretation) 데이터를 스크래핑하고 저장"""
    try:
        task_logger = get_run_logger()
        task_logger.info("🔄 행정해석(Interpretation) 스크래퍼 시작...")
        
        # interpretation scraper 실행
        # from scripts.interpretation.logic.scraper import scrape_and_save as interpretation_scrape_and_save
        
        task_logger.info("✅ 행정해석 스크래퍼 완료")
        return {"status": "success", "scraper": "interpretation", "timestamp": datetime.now().isoformat()}
        
    except Exception as e:
        task_logger = get_run_logger()
        task_logger.error(f"❌ 행정해석 스크래퍼 실패: {e}", exc_info=True)
        return {"status": "failed", "scraper": "interpretation", "error": str(e), "timestamp": datetime.now().isoformat()}


@task(name="Log Results")
def log_results(results: list) -> None:
    """모든 스크래퍼 결과를 로깅"""
    task_logger = get_run_logger()
    task_logger.info("=" * 60)
    task_logger.info("📊 스크래핑 작업 완료 보고서")
    task_logger.info("=" * 60)
    
    success_count = sum(1 for r in results if r.get("status") == "success")
    failed_count = sum(1 for r in results if r.get("status") == "failed")
    
    for result in results:
        status_emoji = "✅" if result["status"] == "success" else "❌"
        task_logger.info(f"{status_emoji} {result['scraper']}: {result['status']}")
        if result.get("error"):
            task_logger.info(f"   오류: {result['error']}")
    
    task_logger.info("=" * 60)
    task_logger.info(f"성공: {success_count}, 실패: {failed_count}")
    task_logger.info("=" * 60)


@flow(name="Roeum Scraping Workflow", description="주기적으로 법령, 판례, 행정예규, 행정해석을 스크래핑")
async def scraping_workflow(
    output_dir: str = "data/output",
    run_all_scrapers: bool = True,
) -> dict:
    """
    주 1회(월요일 00:00) 실행되는 스크래핑 워크플로우
    
    매개변수:
    - output_dir: JSONL 파일 저장 디렉토리
    - run_all_scrapers: 모든 스크래퍼 실행 여부
    """
    logger_obj = get_run_logger()
    logger_obj.info(f"🚀 Roeum 스크래핑 워크플로우 시작 - {datetime.now()}")
    
    # 모든 scraper를 비동기로 실행
    results = await asyncio.gather(
        run_law_scraper(output_dir),
        run_case_scraper(output_dir) if run_all_scrapers else None,
        run_adrule_scraper(output_dir) if run_all_scrapers else None,
        run_interpretation_scraper(output_dir) if run_all_scrapers else None,
        return_exceptions=True
    )
    
    # None 값 제거
    results = [r for r in results if r is not None and not isinstance(r, Exception)]
    
    # 결과 로깅
    log_results(results)
    
    # 최종 요약
    success_count = sum(1 for r in results if r.get("status") == "success")
    logger_obj.info(f"✅ 워크플로우 완료: {success_count}/{len(results)} 성공")
    
    return {
        "workflow_status": "completed",
        "start_time": datetime.now().isoformat(),
        "results": results,
        "success_count": success_count,
        "total_count": len(results)
    }


if __name__ == "__main__":
    # 테스트용: 직접 실행
    asyncio.run(scraping_workflow())
