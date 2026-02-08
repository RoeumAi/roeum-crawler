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
        if scraper_type == "case":
            list_page_url = f"https://www.law.go.kr/LSW/precAstSc.do?menuId=391&subMenuId=397&tabMenuId=443&cptOfiCd={config.dept_code}"
        elif scraper_type == "adrule":
            # [업데이트] 새로운 URL 구조로 변경됨
            list_page_url = f"https://www.law.go.kr/LSW/admRulAstSc.do?menuId=391&subMenuId=397&tabMenuId=441&cptOfiCd={config.dept_code}"
        elif scraper_type == "interpretation":
            # 법령해석 전용 URL (상위 분류 및 하위 분류 포함)
            list_page_url = "https://www.law.go.kr/LSW/cgmExpcSc.do?menuId=11&subMenuId=729&tabMenuId=733&upperOfiClsCd=010501&ofiClsCd=350101"
        else:
            # law, decision, mediation_case 등
            list_page_url = f"https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd={config.dept_code}"
        
        urls = await fetch_urls(
            start_url=list_page_url,
            max_pages_arg=max_pages
        )
        
        logger.info(f"✅ Step 1 완료: {len(urls)}개의 URL 발견")
        
        # URLItem으로 변환하여 검증
        url_items = [
            URLItem(
                name=url.get("name", ""),
                url=url if isinstance(url.get("url"), str) else url  # dict 전체 또는 url 문자열
            )
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
        
        # JSONL 저장 여부 (대용량 크롤링에서는 False로 설정하여 디스크 절약)
        save_jsonl = os.getenv('SAVE_JSONL', 'true').lower() == 'true'
        
        # 스크래핑 실행
        result = await scrape_and_save(
            url=doc_info["url"],
            output_dir=output_dir,
            output_name=f"prefect_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            dept_code=doc_info.get("dept_code", "1492000"),
            save_to_db=True,
            save_jsonl=save_jsonl
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
        
        for idx, result in enumerate(results, 1):
            if isinstance(result, Exception):
                failed_count += 1
                error_msg = str(result)
                logger.warning(f"  [{idx}/{len(urls)}] ❌ 실패: {error_msg}")
                errors.append(error_msg)
            elif isinstance(result, dict) and result.get("status") == "success":
                success_count += 1
                doc_id = result.get("doc_id", "Unknown")
                logger.debug(f"  [{idx}/{len(urls)}] ✅ 성공: {doc_id}")
            else:
                failed_count += 1
                error_msg = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
                logger.warning(f"  [{idx}/{len(urls)}] ❌ 실패: {error_msg}")
                if isinstance(result, dict) and result.get("error"):
                    errors.append(result["error"])
        
        logger.info(f"✅ Step 2 완료: {success_count}/{len(urls)}개 성공, {failed_count}개 실패")
        if errors and len(errors) <= 5:
            logger.warning(f"  에러 목록: {errors}")
        
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
# TASK 3.5: 통합 JSONL 저장 (병렬 처리 최적화)
# ============================================================================

@task(name="Merge JSONL Files", retries=2, retry_delay_seconds=30)
async def merge_jsonl_files_task(
    scraper_type: str,
    total_scraped: int
) -> Dict:
    """
    개별 JSONL 파일들을 하나의 통합 파일로 병합 (병렬 처리 최적화)
    
    병렬 처리로 생성된 개별 JSONL 파일들을 수집하여
    하나의 통합 JSONL 파일로 저장합니다.
    
    Args:
        scraper_type: 스크래퍼 타입
        total_scraped: 스크래핑된 총 문서 수
    
    Returns:
        저장 결과 딕셔너리
    """
    logger = get_run_logger()
    
    try:
        from pathlib import Path
        
        output_dir = os.path.join(project_root, "data", "output")
        
        # 최신 타임스탬프로 통합 파일명 생성
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        merged_filename = os.path.join(output_dir, f"merged_{scraper_type}_{timestamp}_merged.jsonl")
        
        # 최근 파일들 수집 (현재 실행의 개별 파일들)
        all_jsonl_files = sorted(
            Path(output_dir).glob("prefect_*.jsonl"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        # 현재 실행의 파일들만 선택 (최근 total_scraped개)
        recent_files = all_jsonl_files[:max(total_scraped, len(all_jsonl_files))]
        
        merged_docs = []
        doc_count = 0
        
        for jsonl_file in recent_files:
            try:
                with open(jsonl_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            doc = json.loads(line)
                            merged_docs.append(doc)
                            doc_count += 1
            except Exception as e:
                logger.warning(f"⚠️  파일 읽기 실패 {jsonl_file.name}: {e}")
                continue
        
        # 통합 JSONL 파일 생성
        with open(merged_filename, 'w', encoding='utf-8') as f:
            for doc in merged_docs:
                f.write(json.dumps(doc, ensure_ascii=False) + '\n')
        
        logger.info(
            f"✅ JSONL 병합 완료:\n"
            f"   입력: {len(recent_files)}개 파일\n"
            f"   출력: {merged_filename}\n"
            f"   문서: {doc_count}개"
        )
        
        return {
            "status": "success",
            "merged_file": merged_filename,
            "input_files": len(recent_files),
            "output_documents": doc_count,
            "error": None
        }
    
    except Exception as e:
        logger.error(f"❌ JSONL 병합 실패: {str(e)}")
        return {
            "status": "failed",
            "merged_file": None,
            "input_files": 0,
            "output_documents": 0,
            "error": str(e)
        }


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
    
    실제 MongoDB에 저장된 문서 개수를 쿼리해서 반환합니다.
    
    Args:
        scraper_type: 스크래퍼 타입
        total_scraped: 스크래핑된 총 문서 수
    
    Returns:
        저장 결과 딕셔너리
    """
    logger = get_run_logger()
    
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        
        db = get_mongo_db()
        collection = db[scraper_type]
        
        # 실제 MongoDB에 저장된 문서 개수
        actual_count = collection.count_documents({})
        
        logger.info(f"📍 MongoDB 저장 작업 확인:")
        logger.info(f"   - 스크래핑 시도: {total_scraped}개")
        logger.info(f"   - 실제 저장됨: {actual_count}개")
        
        if actual_count != total_scraped:
            logger.warning(f"⚠️  차이: {total_scraped - actual_count}개 (저장 실패 또는 중복)")
        
        return {
            "status": "success",
            "scraped_count": total_scraped,
            "actual_saved_count": actual_count,
            "error": None,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"❌ MongoDB 저장 작업 확인 실패: {str(e)}")
        return {
            "status": "failed",
            "scraped_count": total_scraped,
            "actual_saved_count": 0,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


# ============================================================================
# MAIN FLOW
# ============================================================================

@flow(
    name="unified-scraper",
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
    
    # STEP 3.5: JSONL 병합 (병렬 처리 최적화)
    logger.info("\n" + "="*80)
    logger.info("[STEP 3.5] 📄 JSONL 파일 병합")
    logger.info("="*80)
    
    jsonl_result = await merge_jsonl_files_task(scraper_type, total_success)
    
    if jsonl_result["status"] == "success":
        logger.info(
            f"✅ JSONL 병합 완료:\n"
            f"   입력: {jsonl_result['input_files']}개 파일\n"
            f"   출력: {os.path.basename(jsonl_result['merged_file'])}\n"
            f"   문서: {jsonl_result['output_documents']}개"
        )
    else:
        logger.warning(f"⚠️ JSONL 병합 실패: {jsonl_result.get('error')}")
    
    # STEP 3: 중앙집중식 MongoDB 저장 (현재 각 scraper에서 개별적으로 저장 중)
    logger.info("\n" + "="*80)
    logger.info("[STEP 4] 💾 MongoDB 저장 작업 완료 확인")
    logger.info("="*80)
    
    mongodb_result = await store_in_mongodb_task(scraper_type, total_success)
    
    if mongodb_result["status"] != "success":
        logger.warning(f"⚠️ MongoDB 저장 작업 확인 실패: {mongodb_result.get('error')}")
    else:
        actual_saved = mongodb_result.get('actual_saved_count', 0)
        logger.info(f"✅ MongoDB 저장 작업 완료: {actual_saved}개 문서 저장됨")
    
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
        "jsonl_merged": jsonl_result["status"] == "success",
        "merged_file": jsonl_result.get("merged_file") if jsonl_result["status"] == "success" else None,
        "timestamp": datetime.now().isoformat()
    }
