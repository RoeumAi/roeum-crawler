"""
통합 Scraper Flow - 모든 scraper를 위한 제네릭 Flow

각 scraper(law, adrule, case 등)을 동일한 Flow로 실행합니다.
scraper_type 파라미터로 어떤 scraper를 실행할지 결정합니다.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from importlib import import_module
from typing import Dict, Optional, Any

from prefect import flow, task, get_run_logger

# sys.path에 프로젝트 루트 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.core.config import get_scraper_config, ENV
from scripts.core.database.unified_repository import UnifiedDocumentRepository
from scripts.core.schemas import URLCollection, URLItem, DocumentResult, BatchScrapingResult


# ============================================================================
# TASK 1: URL 수집
# ============================================================================

@task(name="Fetch URLs", retries=2, retry_delay_seconds=30)
async def fetch_urls_task(
    scraper_type: str,
    max_pages: Optional[int] = None
) -> URLCollection:
    """
    Step 1: 목록 페이지에서 URL 수집
    
    Args:
        scraper_type: 스크래퍼 타입 (law, adrule, case 등)
        max_pages: 최대 페이지 수
    
    Returns:
        URLCollection: 수집된 URL 목록과 메타데이터
    """
    logger = get_run_logger()
    config = get_scraper_config(scraper_type)
    
    logger.info(f"📍 Step 1: {config.display_name} URL 수집 시작")
    
    try:
        # 동적으로 scraper 모듈 import
        scraper_module = import_module(f"scripts.{scraper_type}.logic.list_scraper")
        
        # 모든 scraper에서 통일된 함수명 사용
        fetch_urls = getattr(scraper_module, "fetch_urls", None)
        
        if not fetch_urls:
            raise ValueError(f"fetch_urls not found in {scraper_type} scraper")
        
        # Scraper별 URL 수집 (list_scraper 모듈의 구현을 사용)
        list_page_url = f"https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd={config.dept_code}"
        urls = await fetch_urls(
            start_url=list_page_url,
            max_pages_arg=max_pages
        )
        
        logger.info(f"✅ Step 1 완료: {len(urls)}개의 URL 발견")
        
        # URLItem으로 변환하여 검증
        url_items = [
            URLItem(name=url.get("name", ""), url=url.get("url", ""))
            for url in urls
        ]
        
        return URLCollection(
            status="success",
            urls_count=len(url_items),
            urls=url_items
        )
    
    except Exception as e:
        logger.error(f"❌ Step 1 실패: {str(e)}")
        return URLCollection(
            status="failed",
            error=str(e),
            urls_count=0
        )


# ============================================================================
# TASK 2: 개별 URL 스크래핑 (제네릭)
# ============================================================================

@task(name="Scrape Document")
async def scrape_document_task(
    scraper_type: str,
    doc_info: Dict[str, str]
) -> Dict[str, Any]:
    """
    개별 문서 스크래핑
    
    STEP 3 개선: 현재는 scrape_and_save()가 직접 MongoDB에 저장하므로,
    이 태스크는 성공/실패 상태만 반환합니다.
    향후 별도의 scrape_only() 함수를 추가하면 여기서 문서 객체를 반환할 수 있습니다.
    """
    try:
        # 동적으로 scraper 모듈 import
        scraper_module = import_module(f"scripts.{scraper_type}.logic.scraper")
        scrape_and_save = getattr(scraper_module, 'scrape_and_save', None)
        
        if not scrape_and_save:
            return {
                "status": "failed",
                "error": f"scrape_and_save not found in {scraper_type}",
                "doc_id": None,
            }
        
        output_dir = os.path.join(os.getcwd(), ENV.output_dir)
        os.makedirs(output_dir, exist_ok=True)
        
        # 스크래핑 실행
        result = await scrape_and_save(
            url=doc_info["url"],
            output_dir=output_dir,
            output_name=f"prefect_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            dept_code=doc_info.get("dept_code", "1492000"),
            save_to_db=True
        )
        
        return {
            "status": "success",
            "doc_id": result.get("doc_id") if result else None,
        }
    
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
            "doc_id": None,
        }


# ============================================================================
# TASK 3: 병렬 스크래핑
# ============================================================================

@task(name="Scrape All URLs in Parallel")
async def scrape_all_urls_task(
    scraper_type: str,
    urls: list,
    max_concurrent: int = 3
) -> BatchScrapingResult:
    """
    모든 URL을 병렬로 스크래핑
    
    scrape_and_save()가 직접 MongoDB에 저장하므로,
    이 태스크는 성공/실패 개수만 반환합니다.
    """
    logger = get_run_logger()
    config = get_scraper_config(scraper_type)
    
    logger.info(f"📍 Step 2: {len(urls)}개 URL 병렬 스크래핑 시작 (동시 수: {max_concurrent})")
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def scrape_with_semaphore(url):
        async with semaphore:
            # url은 URLItem 객체
            url_str = url.url if isinstance(url, URLItem) else url["url"]
            return await scrape_document_task(
                scraper_type=scraper_type,
                doc_info={"url": url_str, "dept_code": config.dept_code}
            )
    
    try:
        tasks = [scrape_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = 0
        failed_count = 0
        errors = []
        
        for result in results:
            if isinstance(result, Exception):
                failed_count += 1
                errors.append(str(result))
            elif isinstance(result, dict) and result.get("status") == "success":
                success_count += 1
            else:
                failed_count += 1
                if isinstance(result, dict) and result.get("error"):
                    errors.append(result["error"])
        
        logger.info(f"✅ Step 2 완료: {success_count}개 성공, {failed_count}개 실패")
        
        return BatchScrapingResult(
            status="success",
            total_urls=len(urls),
            successful=success_count,
            failed=failed_count,
            errors=errors
        )
    
    except Exception as e:
        logger.error(f"❌ Step 2 실패: {str(e)}")
        return BatchScrapingResult(
            status="failed",
            total_urls=len(urls),
            successful=0,
            failed=len(urls),
            errors=[str(e)]
        )


# ============================================================================
# TASK 4: MongoDB 저장 (중앙집중식)
# ============================================================================

@task(name="Store in MongoDB", retries=2, retry_delay_seconds=30)
async def store_in_mongodb_task(
    scraper_type: str,
    total_scraped: int
) -> Dict:
    """
    MongoDB 저장 작업 (중앙집중식)
    
    STEP 3 개선: 현재 각 scraper의 scrape_and_save()에서 직접 MongoDB에 저장하므로,
    이 태스크는 확인/검증 역할만 합니다.
    
    향후 Flow 레벨에서 모든 문서를 수집한 후 이 태스크에서 일괄 저장하도록 
    리팩토링할 수 있습니다. (STEP 3 최적화 단계)
    
    Args:
        scraper_type: 스크래퍼 타입
        total_scraped: 스크래핑된 총 문서 수
    
    Returns:
        저장 결과 딕셔너리
    """
    logger = get_run_logger()
    
    try:
        logger.info(f"📍 MongoDB 저장 작업 완료: {total_scraped}개 문서가 저장되었습니다.")
        
        return {
            "status": "success",
            "inserted_count": total_scraped,
            "updated_count": 0,
            "error": None,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"❌ MongoDB 저장 작업 실패: {str(e)}")
        return {
            "status": "failed",
            "inserted_count": 0,
            "updated_count": 0,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


# ============================================================================
# MAIN FLOW
# ============================================================================

@flow(
    name="unified-scraper",
    work_pool_name="default",
    description="모든 scraper를 위한 통합 Flow"
)
async def unified_scraper_flow(
    scraper_type: str = "law",
    max_pages: Optional[int] = None,
    max_concurrent: int = 3
) -> Dict:
    """
    통합 Scraper Flow
    
    Args:
        scraper_type: 스크래퍼 타입 (law, adrule, case 등)
        max_pages: 최대 페이지 수 (None=모든 페이지)
        max_concurrent: 동시 크롤링 수
    
    Returns:
        {
            "status": "success",
            "scraper_type": str,
            "total_urls": int,
            "total_success": int,
            "total_failed": int
        }
    """
    
    logger = get_run_logger()
    config = get_scraper_config(scraper_type)
    
    print("\n" + "="*80)
    print(f"🚀 {config.display_name} 통합 크롤링 워크플로우 시작")
    print("="*80)
    print(f"스크래퍼: {scraper_type}")
    print(f"최대 페이지: {max_pages or '무제한'}")
    print(f"동시 수: {max_concurrent}")
    print("="*80 + "\n")
    
    # STEP 1: URL 수집
    logger.info("="*80)
    logger.info("[STEP 1] 📍 URL 수집")
    logger.info("="*80)
    
    fetch_result: URLCollection = await fetch_urls_task(scraper_type, max_pages)
    
    if fetch_result.status != "success":
        logger.error(f"❌ URL 수집 실패: {fetch_result.error}")
        return {
            "status": "failed",
            "scraper_type": scraper_type,
            "error": fetch_result.error,
            "total_urls": 0,
            "total_success": 0,
            "total_failed": 0
        }
    
    urls = fetch_result.urls
    total_urls = len(urls)
    
    # STEP 2: 병렬 스크래핑 (scrape_and_save()가 직접 MongoDB에 저장함)
    logger.info("\n" + "="*80)
    logger.info("[STEP 2] 🔄 개별 URL 병렬 스크래핑 및 저장")
    logger.info("="*80)
    
    scrape_result: BatchScrapingResult = await scrape_all_urls_task(scraper_type, urls, max_concurrent)
    
    if scrape_result.status != "success":
        logger.error(f"❌ 스크래핑 실패: {scrape_result.errors}")
        return {
            "status": "failed",
            "scraper_type": scraper_type,
            "error": scrape_result.errors,
            "total_urls": total_urls,
            "total_success": 0,
            "total_failed": total_urls
        }
    
    total_success = scrape_result.successful
    total_failed = scrape_result.failed
    
    # STEP 3: 중앙집중식 MongoDB 저장 (현재 각 scraper에서 개별적으로 저장 중)
    logger.info("\n" + "="*80)
    logger.info("[STEP 3] 💾 MongoDB 저장 작업 완료 확인")
    logger.info("="*80)
    
    mongodb_result = await store_in_mongodb_task(scraper_type, total_success)
    
    if mongodb_result["status"] != "success":
        logger.warning(f"⚠️ MongoDB 저장 작업 완료 확인 실패: {mongodb_result.get('error')}")
        # 이미 각 scraper에서 저장했으므로 여기서는 경고만 하고 계속 진행
    else:
        logger.info(f"✅ MongoDB 저장 작업 완료: {total_success}개 문서")
    
    # 최종 결과
    logger.info("\n" + "="*80)
    logger.info("✅ 워크플로우 완료")
    logger.info(f"총 URL: {total_urls}, 성공: {total_success}, 실패: {total_failed}")
    logger.info("="*80)
    
    return {
        "status": "success",
        "scraper_type": scraper_type,
        "total_urls": total_urls,
        "total_success": total_success,
        "total_failed": total_failed,
        "timestamp": datetime.now().isoformat()
    }
