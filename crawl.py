#!/usr/bin/env python3
"""
통합 크롤링 스크립트

모든 scraper를 한 번에 또는 선택적으로 실행합니다.

사용법:
    python3 crawl.py                    # 모든 scraper 실행
    python3 crawl.py --scraper law      # law만 실행
    python3 crawl.py --scraper law adrule  # law, adrule만 실행
    python3 crawl.py --list             # 사용 가능한 scraper 목록
    python3 crawl.py --concurrent 5     # 동시 수 변경
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime
import argparse
from importlib import import_module
from typing import Dict, Optional

# 프로젝트 루트 추가
project_root = str(Path(__file__).parent)
sys.path.insert(0, project_root)

from scripts.core.config import get_scraper_config, get_scraper_list, ENV


def parse_args():
    """명령행 인자 파싱"""
    parser = argparse.ArgumentParser(
        description='통합 크롤링 스크립트',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 모든 scraper 실행
  python3 crawl.py
  
  # 특정 scraper만 실행
  python3 crawl.py --scraper law
  python3 crawl.py --scraper law adrule case
  
  # 동시 수 변경
  python3 crawl.py --concurrent 5
  
  # 테스트 (1페이지만)
  python3 crawl.py --pages 1
  
  # 사용 가능한 scraper 목록
  python3 crawl.py --list
        """
    )
    
    parser.add_argument(
        '--scraper',
        nargs='+',
        default=None,
        help='실행할 scraper (미지정 시 모든 scraper 실행)'
    )
    
    parser.add_argument(
        '--concurrent',
        type=int,
        default=3,
        help='동시 크롤링 수 (기본값: 3)'
    )
    
    parser.add_argument(
        '--pages',
        type=int,
        default=None,
        help='최대 페이지 수 (기본값: None = 모든 페이지)'
    )
    
    parser.add_argument(
        '--list',
        action='store_true',
        help='사용 가능한 scraper 목록 표시'
    )
    
    return parser.parse_args()


async def run_single_scraper(scraper_type, max_concurrent, max_pages):
    """단일 scraper 실행 (Prefect 없이 직접 실행)"""
    config = get_scraper_config(scraper_type)
    
    print(f"\n{'='*80}")
    print(f"🚀 {config.display_name} 크롤링 시작 (동시 처리: {max_concurrent})")
    print(f"{'='*80}\n")
    
    try:
        # 동적으로 list_scraper 모듈 import
        list_scraper_module = import_module(f"scripts.{scraper_type}.logic.list_scraper")
        scraper_module = import_module(f"scripts.{scraper_type}.logic.scraper")
        
        fetch_urls = getattr(list_scraper_module, "fetch_urls")
        scrape_and_save = getattr(scraper_module, "scrape_and_save")
        
        # Step 1: URL 수집
        if scraper_type == "case":
            list_page_url = f"https://www.law.go.kr/LSW/precAstSc.do?menuId=391&subMenuId=397&tabMenuId=443&cptOfiCd={config.dept_code}"
        elif scraper_type == "adrule":
            list_page_url = f"https://www.law.go.kr/LSW/admRulAstSc.do?menuId=391&subMenuId=397&tabMenuId=441&cptOfiCd={config.dept_code}"
        elif scraper_type == "interpretation":
            list_page_url = "https://www.law.go.kr/LSW/cgmExpcSc.do?menuId=11&subMenuId=729&tabMenuId=733&upperOfiClsCd=010501&ofiClsCd=350101"
        elif scraper_type == "judgment":
            list_page_url = "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do"
        elif scraper_type == "mediation_case":
            list_page_url = "https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do"
        else:
            list_page_url = f"https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd={config.dept_code}"
        
        print(f"📍 Step 1: URL 수집 시작...")
        urls = await fetch_urls(
            start_url=list_page_url,
            max_pages_arg=max_pages
        )
        print(f"✅ Step 1 완료: {len(urls)}개의 URL 발견\n")
        
        if not urls:
            return {
                "status": "failed",
                "error": "No URLs found",
                "total_urls": 0,
                "total_success": 0,
                "total_failed": 0
            }
        
        # Step 2: 병렬 스크래핑
        print(f"📍 Step 2: {len(urls)}개 URL 병렬 스크래핑 시작 (동시 수: {max_concurrent})")
        
        output_dir = os.path.join(os.getcwd(), ENV.output_dir)
        os.makedirs(output_dir, exist_ok=True)
        
        save_jsonl = os.getenv('SAVE_JSONL', 'true').lower() == 'true'
        
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def scrape_with_semaphore(url_item):
            async with semaphore:
                try:
                    url_str = url_item.get("url") if isinstance(url_item, dict) else url_item
                    result = await scrape_and_save(
                        url=url_str,
                        output_dir=output_dir,
                        output_name=f"direct_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                        dept_code=config.dept_code,
                        save_to_db=True,
                        save_jsonl=save_jsonl
                    )
                    return {"status": "success", "doc_id": result.get("doc_id") if result else None}
                except Exception as e:
                    return {"status": "failed", "error": str(e)}
        
        tasks = [scrape_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 결과 통계
        success_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "success")
        failed_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "failed")
        
        print(f"\n✅ Step 2 완료: {success_count}개 성공, {failed_count}개 실패\n")
        
        return {
            "status": "success" if success_count > 0 else "failed",
            "total_urls": len(urls),
            "total_success": success_count,
            "total_failed": failed_count
        }
    
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return {
            "status": "failed",
            "error": str(e),
            "total_urls": 0,
            "total_success": 0,
            "total_failed": 0
        }


async def run_multiple_scrapers(scrapers, max_concurrent, max_pages):
    """여러 scraper 순차 실행"""
    results = {}
    
    for scraper_type in scrapers:
        result = await run_single_scraper(scraper_type, max_concurrent, max_pages)
        results[scraper_type] = result
    
    return results


def print_results(results):
    """결과 출력"""
    print("\n" + "="*80)
    print("📊 크롤링 최종 결과")
    print("="*80)
    
    total_urls = 0
    total_success = 0
    total_failed = 0
    
    for scraper_type, result in results.items():
        config = get_scraper_config(scraper_type)
        status = "✅" if result.get("status") == "success" else "❌"
        
        print(f"\n{status} {config.display_name} ({scraper_type})")
        print(f"   - 수집 URL: {result.get('total_urls', 0):,}개")
        print(f"   - 성공: {result.get('total_success', 0):,}개")
        print(f"   - 실패: {result.get('total_failed', 0):,}개")
        
        total_urls += result.get('total_urls', 0)
        total_success += result.get('total_success', 0)
        total_failed += result.get('total_failed', 0)
    
    print(f"\n{'='*80}")
    print(f"📈 전체 통계")
    print(f"{'='*80}")
    print(f"   - 전체 수집 URL: {total_urls:,}개")
    print(f"   - 전체 성공: {total_success:,}개")
    print(f"   - 전체 실패: {total_failed:,}개")
    print(f"   - 성공률: {total_success/max(total_urls, 1)*100:.1f}%")
    print(f"\n💾 저장 위치: MongoDB (original_db)")
    print("="*80 + "\n")


def main():
    """메인 함수"""
    args = parse_args()
    
    # --list 옵션 처리
    if args.list:
        print("\n📋 사용 가능한 Scraper 목록:")
        print("="*80)
        for name in get_scraper_list():
            config = get_scraper_config(name)
            print(f"  - {name:20} : {config.display_name}")
        print("="*80 + "\n")
        return 0
    
    # 실행할 scraper 결정
    if args.scraper is None:
        scrapers = get_scraper_list()
        print(f"\n📌 옵션: 모든 scraper 실행 ({', '.join(scrapers)})")
    else:
        scrapers = args.scraper
        print(f"\n📌 옵션: {', '.join(scrapers)} 실행")
    
    # 설정 출력
    print(f"\n🔧 크롤링 설정:")
    print(f"   - 동시 수: {args.concurrent}")
    print(f"   - 최대 페이지: {args.pages or '무제한'}")
    
    start_time = datetime.now()
    print(f"\n⏰ 시작 시간: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    try:
        # 크롤링 실행
        if len(scrapers) == 1:
            result = asyncio.run(run_single_scraper(
                scrapers[0],
                args.concurrent,
                args.pages
            ))
            results = {scrapers[0]: result}
        else:
            results = asyncio.run(run_multiple_scrapers(
                scrapers,
                args.concurrent,
                args.pages
            ))
        
        # 결과 출력
        print_results(results)
        
        end_time = datetime.now()
        elapsed = (end_time - start_time).total_seconds() / 60
        
        print(f"🏁 종료 시간: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⏱️  소요 시간: {elapsed:.1f}분 ({int(elapsed // 60)}시간 {int(elapsed % 60)}분)")
        print()
        
        return 0
    
    except KeyboardInterrupt:
        print("\n\n⚠️  사용자가 중단했습니다.")
        return 1
    
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
