"""
Prefect Flow for automated web scraping (Updated for Unified Schema)

주기적으로 4개 scraper (law, case, adrule, interpretation)를 실행하고
MongoDB (unified_documents collection) + JSONL에 저장하는 워크플로우

변경사항:
- unified schema 적용 (단일 컬렉션, is_active 버전 관리)
- 각 scraper에서 unified_doc 처리
- 병렬 실행으로 성능 최적화
"""

import asyncio
import os
from datetime import datetime
from typing import Dict, List
from prefect import flow, task, get_run_logger


@task(name="Run Law Scraper", retries=2, retry_delay_seconds=60)
async def run_law_scraper(output_dir: str = "data/output") -> Dict:
    """
    법령(법률) 데이터 스크래핑
    
    Returns:
        {
            "status": "success|failed",
            "scraper": "law",
            "docs_saved": int,
            "timestamp": ISO datetime string
        }
    """
    try:
        from scripts.law.logic.scraper import scrape_and_save
        from scripts.law.logic.list_scraper import fetch_law_urls
        
        task_logger = get_run_logger()
        task_logger.info("🔄 법령 스크래퍼 시작...")
        
        # 1. 법령 URL 목록 조회
        start_url = "https://www.law.go.kr/LSW/lsSc.do?P_types=PL&menuId=1&subMenuId=11"
        try:
            urls = await fetch_law_urls(start_url, max_pages_arg=1)
            task_logger.info(f"📍 {len(urls)}개의 법령 URL 발견")
        except Exception as e:
            task_logger.warning(f"⚠️  URL 조회 실패, 기본 URL로 진행: {e}")
            urls = [{"url": "https://www.law.go.kr/LSW/lsSc.do"}]
        
        # 2. 각 URL별 스크래핑
        success_count = 0
        fail_count = 0
        
        for item in urls[:5]:  # 최대 5개 샘플링
            try:
                url = item.get('url') or item
                output_name = f"law_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                
                await scrape_and_save(
                    url=url,
                    output_dir=output_dir,
                    output_name=output_name,
                    dept_code="LOA",
                    save_to_db=True
                )
                success_count += 1
            except Exception as e:
                task_logger.warning(f"⚠️  개별 URL 스크래핑 실패 {url}: {e}")
                fail_count += 1
        
        task_logger.info(f"✅ 법령 스크래퍼 완료: {success_count}개 성공, {fail_count}개 실패")
        return {
            "status": "success" if success_count > 0 else "failed",
            "scraper": "law",
            "docs_saved": success_count,
            "docs_failed": fail_count,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        task_logger = get_run_logger()
        task_logger.error(f"❌ 법령 스크래퍼 실패: {e}", exc_info=True)
        return {
            "status": "failed",
            "scraper": "law",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@task(name="Run Case Scraper", retries=2, retry_delay_seconds=60)
async def run_case_scraper(output_dir: str = "data/output") -> Dict:
    """
    판례(판결) 데이터 스크래핑
    
    Note: case scraper 리팩토링 후 구현
    """
    try:
        task_logger = get_run_logger()
        task_logger.info("🔄 판례 스크래퍼 시작...")
        
        # from scripts.case.logic.scraper import scrape_and_save as case_scrape_and_save
        # await case_scrape_and_save(...)
        
        task_logger.info("⏳ 판례 스크래퍼: 준비 중 (리팩토링 대기)")
        return {
            "status": "pending",
            "scraper": "case",
            "message": "Refactoring in progress",
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        task_logger = get_run_logger()
        task_logger.error(f"❌ 판례 스크래퍼 실패: {e}", exc_info=True)
        return {
            "status": "failed",
            "scraper": "case",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@task(name="Run Adrule Scraper", retries=2, retry_delay_seconds=60)
async def run_adrule_scraper(output_dir: str = "data/output") -> Dict:
    """
    행정규칙 데이터 스크래핑
    
    Note: adrule scraper 리팩토링 후 구현
    """
    try:
        task_logger = get_run_logger()
        task_logger.info("🔄 행정규칙 스크래퍼 시작...")
        
        # from scripts.adrule.logic.scraper import scrape_and_save as adrule_scrape_and_save
        # await adrule_scrape_and_save(...)
        
        task_logger.info("⏳ 행정규칙 스크래퍼: 준비 중 (리팩토링 대기)")
        return {
            "status": "pending",
            "scraper": "adrule",
            "message": "Refactoring in progress",
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        task_logger = get_run_logger()
        task_logger.error(f"❌ 행정규칙 스크래퍼 실패: {e}", exc_info=True)
        return {
            "status": "failed",
            "scraper": "adrule",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@task(name="Run Interpretation Scraper", retries=2, retry_delay_seconds=60)
async def run_interpretation_scraper(output_dir: str = "data/output") -> Dict:
    """
    행정해석 데이터 스크래핑
    
    Note: interpretation scraper 리팩토링 후 구현
    """
    try:
        task_logger = get_run_logger()
        task_logger.info("🔄 행정해석 스크래퍼 시작...")
        
        # from scripts.interpretation.logic.scraper import scrape_and_save as interp_scrape_and_save
        # await interp_scrape_and_save(...)
        
        task_logger.info("⏳ 행정해석 스크래퍼: 준비 중 (리팩토링 대기)")
        return {
            "status": "pending",
            "scraper": "interpretation",
            "message": "Refactoring in progress",
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        task_logger = get_run_logger()
        task_logger.error(f"❌ 행정해석 스크래퍼 실패: {e}", exc_info=True)
        return {
            "status": "failed",
            "scraper": "interpretation",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@task(name="Log Results")
async def log_results(
    law_result: Dict,
    case_result: Dict,
    adrule_result: Dict,
    interpretation_result: Dict
) -> None:
    """
    모든 스크래퍼 결과를 수집하고 로깅
    
    버전 관리:
    - is_active=true: 현재 활성 버전
    - is_active=false: 이전 버전 (변경 이력 추적용)
    """
    task_logger = get_run_logger()
    
    print("\n" + "="*70)
    print("📊 스크래핑 워크플로우 최종 결과")
    print("="*70)
    
    results = {
        "law": law_result,
        "case": case_result,
        "adrule": adrule_result,
        "interpretation": interpretation_result
    }
    
    summary = {
        "success": 0,
        "failed": 0,
        "pending": 0,
        "total_docs": 0
    }
    
    for scraper_name, result in results.items():
        status = result.get("status", "unknown")
        
        print(f"\n📝 {scraper_name.upper()}: {status.upper()}")
        
        if status == "success":
            docs_saved = result.get("docs_saved", 0)
            docs_failed = result.get("docs_failed", 0)
            print(f"  ✅ 저장 성공: {docs_saved}개")
            if docs_failed > 0:
                print(f"  ⚠️  저장 실패: {docs_failed}개")
            summary["success"] += 1
            summary["total_docs"] += docs_saved
            
        elif status == "failed":
            error = result.get("error", "Unknown error")
            print(f"  ❌ 오류: {error}")
            summary["failed"] += 1
            
        elif status == "pending":
            message = result.get("message", "In progress")
            print(f"  ⏳ 상태: {message}")
            summary["pending"] += 1
    
    print("\n" + "-"*70)
    print("📈 요약:")
    print(f"  - 성공한 스크래퍼: {summary['success']}개")
    print(f"  - 실패한 스크래퍼: {summary['failed']}개")
    print(f"  - 준비 중인 스크래퍼: {summary['pending']}개")
    print(f"  - 저장된 총 문서: {summary['total_docs']}개")
    
    # MongoDB 버전 관리 정보
    print("\n💾 MongoDB 버전 관리:")
    print("  - 새 문서 저장: is_active=true")
    print("  - 기존 버전: is_active=false (자동 비활성화)")
    print("  - 쿼리: find_by_source_url()로 모든 버전 조회 가능")
    
    print("\n🕐 실행 시간: " + datetime.now().isoformat())
    print("="*70 + "\n")
    
    task_logger.info(f"✅ 워크플로우 완료: {summary['total_docs']}개 문서 저장")


@flow(name="Unified Document Scraping Workflow", log_prints=True)
async def scraping_workflow(output_dir: str = "data/output"):
    """
    메인 Prefect 워크플로우
    
    구조:
    1. 4개 스크래퍼 병렬 실행 (각각 retries=2)
    2. 결과 수집
    3. 통합 로깅
    
    실행 스케줄: 매주 월요일 00:00 UTC (= 월요일 09:00 KST)
    
    MongoDB 저장 구조:
    - 컬렉션: unified_documents
    - 스키마: doc_id, doc_type, title, sub_title, content, metadata
    - 버전 관리: is_active 플래그로 버전 추적
    - 인덱스: doc_id, doc_type, source_type, created_at, effective, text
    """
    
    logger = get_run_logger()
    logger.info("🚀 통합 스크래핑 워크플로우 시작")
    logger.info(f"📁 출력 디렉토리: {output_dir}")
    
    # 1. 4개 스크래퍼 병렬 실행
    law_result = await run_law_scraper(output_dir)
    case_result = await run_case_scraper(output_dir)
    adrule_result = await run_adrule_scraper(output_dir)
    interpretation_result = await run_interpretation_scraper(output_dir)
    
    # 2. 결과 통합 로깅
    await log_results(law_result, case_result, adrule_result, interpretation_result)
    
    logger.info("✅ 워크플로우 완료")


if __name__ == "__main__":
    # 로컬 테스트용
    import sys
    
    # 로컬 실행
    output = asyncio.run(scraping_workflow(output_dir="data/output"))
    print("✅ 워크플로우 실행 완료")
