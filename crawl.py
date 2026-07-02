#!/usr/bin/env python3
"""
통합 크롤링 스크립트

모든 scraper를 한 번에 또는 선택적으로 실행합니다.

사용법:
    python3 crawl.py                            # 모든 scraper 전체 실행
    python3 crawl.py --scraper law              # law만 전체 실행
    python3 crawl.py --mode update --scraper case  # case 증분 업데이트
    python3 crawl.py --list                     # 사용 가능한 scraper 목록
    python3 crawl.py --concurrent 5             # 동시 수 변경

--mode 옵션:
    full   (기본): 전체 URL을 처음부터 끝까지 크롤링합니다.
    update : MongoDB에 이미 존재하는 URL은 건너뜁니다.
             최근 N일 이내에 크롤링된 문서를 제외하여 증분 업데이트를 수행합니다.
             --since 옵션으로 기준 일수를 지정합니다 (기본: 7일).
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
from importlib import import_module
from typing import Dict, Optional, List, Set

project_root = str(Path(__file__).parent)
sys.path.insert(0, project_root)

from scripts.core.config import get_scraper_config, get_scraper_list, ENV


# ============================================================================
# URL 아이템에서 리스트 페이지 URL 반환 (스크래퍼별 분기)
# ============================================================================

def get_list_page_url(scraper_type: str, config) -> str:
    """스크래퍼 타입에 맞는 목록 페이지 URL 반환"""
    if scraper_type == "case":
        return f"https://www.law.go.kr/LSW/precAstSc.do?menuId=391&subMenuId=397&tabMenuId=443&cptOfiCd={config.dept_code}"
    elif scraper_type == "adrule":
        return f"https://www.law.go.kr/LSW/admRulAstSc.do?menuId=391&subMenuId=397&tabMenuId=441&cptOfiCd={config.dept_code}"
    elif scraper_type == "interpretation":
        return "https://www.law.go.kr/LSW/cgmExpcSc.do?menuId=11&subMenuId=729&tabMenuId=733&upperOfiClsCd=010501&ofiClsCd=350101"
    elif scraper_type == "judgment":
        return "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do"
    elif scraper_type == "mediation_case":
        return "https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do"
    elif scraper_type == "decision":
        return "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do"
    else:
        # law, decision 등 나머지
        return f"https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd={config.dept_code}"


# ============================================================================
# UPDATE 모드: 이미 크롤링된 URL 조회
# ============================================================================

def get_all_crawled_urls(collection_name: str) -> Set[str]:
    """
    MongoDB에서 컬렉션의 모든 source_url과 doc_id 집합을 반환합니다.

    case/mediation_case/judgment/interpretation처럼 내용이 거의 변하지 않는
    스크래퍼에서 신규 문서만 크롤링할 때 사용합니다.
    """
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        collection = db[collection_name]

        docs = collection.find(
            {"metadata.is_active": True},
            {"metadata.source_url": 1, "doc_id": 1},
        ).max_time_ms(15000)

        crawled = set()
        for doc in docs:
            source_url = doc.get("metadata", {}).get("source_url", "")
            if source_url:
                crawled.add(source_url)
            doc_id = doc.get("doc_id", "")
            if doc_id:
                crawled.add(str(doc_id))
        return crawled

    except Exception as e:
        print(f"⚠️  MongoDB 전체 URL 조회 실패 (update 모드 필터 건너뜀): {e}")
        return set()


def get_recently_crawled_urls(collection_name: str, since_days: int) -> Set[str]:
    """
    MongoDB에서 최근 since_days일 이내에 크롤링된 source_url 집합을 반환합니다.

    update 모드에서 이미 존재하는 URL을 건너뛰기 위해 사용합니다.

    Args:
        collection_name: MongoDB 컬렉션명
        since_days: 기준 일수 (이 기간 이내에 크롤링된 URL은 제외)

    Returns:
        source_url 문자열 집합
    """
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        collection = db[collection_name]

        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()

        # last_check_at 또는 created_at이 cutoff 이후인 문서의 source_url 수집
        docs = collection.find(
            {
                "$or": [
                    {"metadata.last_check_at": {"$gte": cutoff}},
                    {"metadata.created_at": {"$gte": cutoff}},
                ],
                "metadata.is_active": True,
            },
            {"metadata.source_url": 1, "doc_id": 1}
        ).max_time_ms(5000)

        crawled_urls = set()
        for doc in docs:
            source_url = doc.get("metadata", {}).get("source_url", "")
            if source_url:
                crawled_urls.add(source_url)
            doc_id = doc.get("doc_id", "")
            if doc_id:
                crawled_urls.add(str(doc_id))

        return crawled_urls

    except Exception as e:
        print(f"⚠️  MongoDB 조회 실패 (update 모드 필터 건너뜀): {e}")
        return set()


def get_crawled_effective_dates(collection_name: str, url_list: list | None = None) -> dict:
    """
    MongoDB에서 컬렉션의 source_url → effective 매핑을 반환합니다.

    list_scraper가 effective(시행일자) 필드를 제공하는 스크래퍼(law, adrule)에서
    시행일자 변경 여부로 재크롤링 여부를 결정하는 데 사용합니다.

    Returns:
        {source_url: effective_date_str} 딕셔너리
    """
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        collection = db[collection_name]

        if url_list:
            # 당일 발견된 URL만 조회 - 전체 스캔 타임아웃 방지
            query = {"metadata.is_active": True, "metadata.source_url": {"$in": url_list}}
        else:
            query = {"metadata.is_active": True, "metadata.source_url": {"$exists": True}}

        docs = collection.find(
            query,
            {"metadata.source_url": 1, "metadata.effective": 1},
        ).max_time_ms(60000)

        return {
            doc["metadata"]["source_url"]: doc.get("metadata", {}).get("effective", "")
            for doc in docs
            if doc.get("metadata", {}).get("source_url")
        }

    except Exception as e:
        print(f"⚠️  MongoDB effective 조회 실패 (전체 재크롤링): {e}")
        return {}


def filter_new_urls(url_items: List, crawled_urls: Set[str],
                    effective_map: dict | None = None) -> List:
    """
    이미 크롤링된 URL을 제외하고 새로운 URL만 반환합니다.

    effective_map이 제공되면 URL 존재 여부와 함께 시행일자 변경 여부도 확인합니다.
    - URL 없음 → 신규 크롤링
    - URL 있음 + effective 동일 → 건너뜀
    - URL 있음 + effective 변경 → 재크롤링 (법령 개정)

    Args:
        url_items: list_scraper가 반환한 URL 아이템 목록
        crawled_urls: 이미 크롤링된 source_url 집합 (effective_map 없을 때 사용)
        effective_map: {source_url: effective} 딕셔너리 (law/adrule용)
    """
    new_items = []
    skipped = 0

    for item in url_items:
        url_str = item.get("url") or item.get("doc_seq") if isinstance(item, dict) else item

        if effective_map is not None:
            # 시행일자 기반 비교 (YYYYMMDD ↔ YYYY-MM-DD 정규화)
            if url_str not in effective_map:
                new_items.append(item)  # 신규
            else:
                stored_effective = (effective_map[url_str] or "").replace("-", "")
                item_effective = (item.get("effective", "") if isinstance(item, dict) else "").replace("-", "")
                if stored_effective != item_effective:
                    new_items.append(item)  # 개정됨
                else:
                    skipped += 1
        else:
            # 기존 방식: URL 존재 여부만 확인
            if url_str in crawled_urls:
                skipped += 1
            else:
                new_items.append(item)

    if skipped > 0:
        print(f"🔍 update 모드: {skipped}개 기존 URL 건너뜀, {len(new_items)}개 신규/변경 크롤링 예정")
    return new_items


# ============================================================================
# 단일 스크래퍼 실행
# ============================================================================

async def run_single_scraper(
    scraper_type: str,
    max_concurrent: int,
    max_pages: Optional[int],
    mode: str = "full",
    since_days: int = 7,
) -> Dict:
    """
    단일 scraper 실행 (Prefect 없이 직접 실행)

    Args:
        scraper_type: 스크래퍼 타입
        max_concurrent: 동시 처리 수
        max_pages: 최대 페이지 수 (None = 무제한)
        mode: "full" (전체) 또는 "update" (증분)
        since_days: update 모드에서 기준 일수
    """
    config = get_scraper_config(scraper_type)

    print(f"\n{'='*80}")
    print(f"🚀 {config.display_name} 크롤링 시작 (모드: {mode}, 동시 처리: {max_concurrent})")
    print(f"{'='*80}\n")

    try:
        list_scraper_module = import_module(f"scripts.{scraper_type}.logic.list_scraper")
        scraper_module = import_module(f"scripts.{scraper_type}.logic.scraper")

        fetch_urls = getattr(list_scraper_module, "fetch_urls")
        scrape_and_save = getattr(scraper_module, "scrape_and_save")

        list_page_url = get_list_page_url(scraper_type, config)

        # Step 1: URL 수집
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

        # Step 1.5 (update 모드): 이미 크롤링된 URL 제거
        total_discovered = len(urls)
        if mode == "update":
            # law/adrule: 시행일자 기반 변경 감지 (더 정확)
            # 스크래퍼별 필터 전략
            # - law/adrule: 시행일자 기반 변경 감지 (개정 여부 정확 판별)
            # - case/mediation_case/judgment/interpretation: URL/ID 존재 여부 (내용 불변)
            # - decision: 날짜 기반 (건수 적고 신규 위주)
            if scraper_type in ("law", "adrule"):
                print(f"📍 Step 1.5: update 모드 — 시행일자 기반 변경 감지 중...")
                url_str_list = [
                    (item.get("url") if isinstance(item, dict) else item)
                    for item in urls if item
                ]
                effective_map = get_crawled_effective_dates(config.collection_name, url_list=url_str_list)
                urls = filter_new_urls(urls, set(), effective_map=effective_map)
            elif scraper_type in ("case", "mediation_case", "judgment", "interpretation", "decision"):
                print(f"📍 Step 1.5: update 모드 — 기존 URL/ID 존재 여부로 신규 항목만 필터링 중...")
                crawled_urls = get_all_crawled_urls(config.collection_name)
                urls = filter_new_urls(urls, crawled_urls)
            else:
                print(f"📍 Step 1.5: update 모드 — 최근 {since_days}일 이내 크롤링된 URL 필터링 중...")
                crawled_urls = get_recently_crawled_urls(config.collection_name, since_days)
                urls = filter_new_urls(urls, crawled_urls)
            print(f"✅ Step 1.5 완료: {total_discovered}개 중 {len(urls)}개 신규/변경 크롤링 대상\n")

            if not urls:
                print(f"ℹ️  신규/변경 항목이 없습니다. 건너뜁니다.")
                return {
                    "status": "success",
                    "total_urls": total_discovered,
                    "total_success": 0,
                    "total_failed": 0,
                    "skipped": total_discovered,
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
                    # url_item을 그대로 전달 — scraper가 dict/str 모두 처리
                    result = await scrape_and_save(
                        url=url_item,
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

        success_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "success")
        failed_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "failed")

        print(f"\n✅ Step 2 완료: {success_count}개 성공, {failed_count}개 실패\n")

        return {
            "status": "success" if success_count > 0 else "failed",
            "total_urls": total_discovered,
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


def _run_scraper_in_thread(args):
    """스레드 내에서 단일 스크래퍼 실행 (자체 이벤트 루프)"""
    scraper_type, max_concurrent, max_pages, mode, since_days = args
    return asyncio.run(run_single_scraper(scraper_type, max_concurrent, max_pages, mode, since_days))

async def run_multiple_scrapers(scrapers, max_concurrent, max_pages, mode, since_days):
    """여러 scraper 병렬 실행 (스레드풀 — 동기 I/O 블로킹 우회)"""
    import concurrent.futures
    loop = asyncio.get_event_loop()
    args_list = [(s, max_concurrent, max_pages, mode, since_days) for s in scrapers]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(scrapers)) as pool:
        futures = [loop.run_in_executor(pool, _run_scraper_in_thread, args) for args in args_list]
        task_results = await asyncio.gather(*futures, return_exceptions=True)
    results = {}
    for scraper_type, result in zip(scrapers, task_results):
        if isinstance(result, Exception):
            results[scraper_type] = {"status": "failed", "error": str(result), "total_urls": 0, "total_success": 0, "total_failed": 0}
        else:
            results[scraper_type] = result
    return results


# ============================================================================
# 결과 출력
# ============================================================================

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
        print(f"   - 발견 URL: {result.get('total_urls', 0):,}개")
        print(f"   - 성공: {result.get('total_success', 0):,}개")
        print(f"   - 실패: {result.get('total_failed', 0):,}개")
        if result.get("skipped"):
            print(f"   - 건너뜀: {result.get('skipped', 0):,}개 (이미 크롤링됨)")

        total_urls += result.get('total_urls', 0)
        total_success += result.get('total_success', 0)
        total_failed += result.get('total_failed', 0)

    print(f"\n{'='*80}")
    print(f"📈 전체 통계")
    print(f"{'='*80}")
    print(f"   - 전체 발견 URL: {total_urls:,}개")
    print(f"   - 전체 성공: {total_success:,}개")
    print(f"   - 전체 실패: {total_failed:,}개")
    if total_urls > 0:
        print(f"   - 성공률: {total_success / total_urls * 100:.1f}%")
    print(f"\n💾 저장 위치: MongoDB (original_db)")
    print("="*80 + "\n")


# ============================================================================
# CLI
# ============================================================================

def parse_args():
    """명령행 인자 파싱"""
    parser = argparse.ArgumentParser(
        description='통합 크롤링 스크립트',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 전체 크롤링 (모든 스크래퍼)
  python3 crawl.py

  # 특정 스크래퍼 전체 크롤링
  python3 crawl.py --scraper law
  python3 crawl.py --scraper law adrule case

  # 증분 업데이트 (최근 7일 이내 크롤링된 URL 건너뜀)
  python3 crawl.py --mode update --scraper case
  python3 crawl.py --mode update --since 3    # 3일 이내 건너뜀

  # 동시 수 변경 / 테스트 (1페이지만)
  python3 crawl.py --concurrent 5
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
        '--mode',
        choices=['full', 'update'],
        default='full',
        help=(
            '크롤링 모드 (기본값: full)\n'
            '  full  : 전체 크롤링 (모든 URL 처리)\n'
            '  update: 증분 업데이트 (최근 크롤링된 URL 건너뜀)'
        )
    )
    parser.add_argument(
        '--since',
        type=int,
        default=7,
        help='update 모드에서 기준 일수 (이 기간 이내 크롤링된 URL 건너뜀, 기본값: 7)'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='사용 가능한 scraper 목록 표시'
    )

    return parser.parse_args()


def main():
    """메인 함수"""
    args = parse_args()

    if args.list:
        print("\n📋 사용 가능한 Scraper 목록:")
        print("="*80)
        for name in get_scraper_list():
            config = get_scraper_config(name)
            print(f"  - {name:20} : {config.display_name}")
        print("="*80 + "\n")
        return 0

    if args.scraper is None:
        scrapers = get_scraper_list()
        print(f"\n📌 옵션: 모든 scraper 실행 ({', '.join(scrapers)})")
    else:
        scrapers = args.scraper
        print(f"\n📌 옵션: {', '.join(scrapers)} 실행")

    print(f"\n🔧 크롤링 설정:")
    print(f"   - 모드: {args.mode}")
    print(f"   - 동시 수: {args.concurrent}")
    print(f"   - 최대 페이지: {args.pages or '무제한'}")
    if args.mode == "update":
        print(f"   - 건너뜀 기준: 최근 {args.since}일 이내 크롤링된 URL")

    start_time = datetime.now()
    print(f"\n⏰ 시작 시간: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)

    try:
        if len(scrapers) == 1:
            result = asyncio.run(run_single_scraper(
                scrapers[0], args.concurrent, args.pages, args.mode, args.since
            ))
            results = {scrapers[0]: result}
        else:
            results = asyncio.run(run_multiple_scrapers(
                scrapers, args.concurrent, args.pages, args.mode, args.since
            ))

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
