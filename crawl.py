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
    update : MongoDB에 이미 있는 문서는 건너뜁니다(법령·행정규칙은 시행일자가 바뀐 것만 다시 수집).
"""

import asyncio
import json
import re
import sys
import os
import time
from pathlib import Path
from datetime import datetime
import argparse
from importlib import import_module
from typing import Dict, Optional, List, Set

project_root = str(Path(__file__).parent)
sys.path.insert(0, project_root)

from scripts.core.config import (
    ENV, SCRAPERS, UPDATE_BY_DOC_ID, UPDATE_BY_EFFECTIVE_DATE, get_scraper_config, get_scraper_list,
)
from scripts.core.run_report import OTHER, classify_failure, note_reason, start_run_report
from scripts.report.batch_result import is_listing_failure


# ============================================================================
# 종료 코드 (run_daily.sh 의 재시도 판단에 사용)
# ============================================================================

EXIT_OK = 0                 # 정상 (개별 문서·목록 페이지 일부 실패, 스크래퍼 내부 예상 밖 오류는 결과의 failed 로 보고)
EXIT_FATAL = 1              # 실행 자체가 중단됨 (예외/사용자 중단)
EXIT_LISTING_FAILURE = 2    # URL 목록조차 수집 못한 스크래퍼 존재 → 외부 수집원 일시 장애 가능성, 재시도 대상


def compute_exit_code(results: Dict[str, dict]) -> int:
    """스크래퍼 결과 딕셔너리로 종료 코드를 결정한다.

    URL 목록 수집 단계에서 실패(발견 URL 0개)한 스크래퍼가 하나라도 있으면
    law.go.kr 등 외부 수집원의 일시 장애(점검·404)일 가능성이 높으므로
    EXIT_LISTING_FAILURE(2)를 반환해 상위 스크립트가 전체 재시도를 하도록 한다.

    개별 문서 단위 실패(목록은 정상 수집)만 있는 경우는 이미 문서 단위로 재시도되고
    다음 실행에서 자연히 따라잡히므로 전체 재시도 대상으로 보지 않는다(EXIT_OK).

    스크래퍼 안에서 목록 수집 밖의 예상 밖 오류(DB 인증 실패·코드 오류 등)가 난 경우
    (error_kind == "unexpected")는 15분 뒤 다시 해도 같은 오류라 목록 실패로 보지 않는다.
    결과의 failed(1건, `<스크래퍼>:run`)로 보고되어 일일 요약에 드러난다.
    """
    for result in results.values():
        if not isinstance(result, dict):
            continue
        if is_listing_failure(result):
            return EXIT_LISTING_FAILURE
    return EXIT_OK


ERROR_KIND_UNEXPECTED = "unexpected"


def unexpected_error_result(scraper_type: str, error, total_urls: int = 0, reasons: dict | None = None) -> Dict:
    """목록 수집 밖에서 난 예상 밖 오류의 결과. 목록 실패(재시도 대상)와 구분되도록 error_kind 를 단다."""
    result = {
        "status": "failed",
        "error": str(error),
        "error_kind": ERROR_KIND_UNEXPECTED,
        "total_urls": total_urls,
        "total_success": 0,
        "total_failed": 1,
        "failed_targets": [f"{scraper_type}:run"],
    }
    if reasons:
        result["failure_reasons"] = reasons
    return result


def note_document_failure(error) -> None:
    """문서 하나가 실패한 원인을 실행 보고의 원인별 건수에 센다.

    원인을 분류할 수 없는 실패는 세지 않는다. 일일 요약이 '기타 저장 실패'로 따로 세기 때문이다.
    """
    reason = classify_failure(error)
    if reason != OTHER:
        note_reason(reason)


# ============================================================================
# UPDATE 모드: 이미 크롤링된 URL 조회
# ============================================================================
# 컬렉션마다 다른 점(목록 참조 URL·update 필터 방식·재수집 필터)은 scripts/core/config.py 의
# SCRAPERS 항목에 있다. 여기서는 그 값을 읽기만 한다.

def _config_by_collection(collection_name: str):
    for config in SCRAPERS.values():
        if config.collection_name == collection_name:
            return config
    return None


def get_retry_doc_id_filter(collection_name: str) -> Optional[dict]:
    """첨부 추출·본문 확인을 다시 해야 하는 doc_id 를 고르는 필터. 해당 없으면 None."""
    config = _config_by_collection(collection_name)
    return dict(config.retry_filter) if config and config.retry_filter else None


def get_save_incomplete_filter(collection_name: str) -> Optional[dict]:
    """일부 섹션만 저장된 현재(active) 문서를 고르는 필터. 해당 없으면 None."""
    config = _config_by_collection(collection_name)
    if config and config.retry_save_incomplete:
        return {"metadata.is_active": True, "metadata.save_incomplete": True}
    return None


def candidate_doc_ids(url_items: List) -> Set[str]:
    """목록 항목에서 filter_new_urls 가 맞춰 볼 값(URL 그대로, URL의 Seq·Sn 번호)을 모은다."""
    candidates: Set[str] = set()
    for item in url_items:
        url_str = item.get("url") or item.get("doc_seq") if isinstance(item, dict) else item
        if not url_str:
            continue
        candidates.add(str(url_str))
        m = re.search(r'(?:Seq|Sn)=(\d+)', str(url_str))
        if m:
            candidates.add(m.group(1))
    return candidates


DOC_ID_LOOKUP_BATCH = 1000


def get_all_crawled_urls(collection_name: str, url_items: List | None = None) -> Set[str]:
    """
    이미 수집한 doc_id 집합을 반환한다. filter_new_urls 가 URL·URL의 번호와 맞춰 본다.

    url_items 를 주면 그날 목록에 나온 후보만 $in 으로 조회한다. 컬렉션 전체 distinct 는
    판례처럼 큰 컬렉션에서 결과 16MB 한도에 걸릴 수 있다. doc_id 가 숫자로 저장된 문서도 있어
    숫자 문자열은 정수로도 함께 찾는다.
    첨부 추출 재시도·본문 없음·일부 저장 대상(get_retry_doc_id_filter, get_save_incomplete_filter)은 집합에서 빼서 다시 수집되게 한다.

    조회 실패는 예외로 올린다. 빈 집합으로 넘기면 전체를 신규로 보고 대량 재수집한다.
    """
    from scripts.core.database.mongo_client import get_mongo_db
    collection = get_mongo_db()[collection_name]
    retry_filters = [f for f in (get_retry_doc_id_filter(collection_name),
                                 get_save_incomplete_filter(collection_name)) if f]

    def lookup(extra: dict | None) -> Set[str]:
        if url_items is None:
            return {str(d) for d in collection.distinct("doc_id", extra or {}) if d}
        found: Set[str] = set()
        values = sorted(candidate_doc_ids(url_items))
        for start in range(0, len(values), DOC_ID_LOOKUP_BATCH):
            batch = values[start:start + DOC_ID_LOOKUP_BATCH]
            keys = batch + [int(v) for v in batch if v.isdigit()]
            query = {"doc_id": {"$in": keys}, **(extra or {})}
            found |= {str(d) for d in collection.distinct("doc_id", query) if d}
        return found

    doc_ids = lookup(None)
    for retry_filter in retry_filters:
        doc_ids -= lookup(retry_filter)
    return doc_ids


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

        # 비활성 문서도 포함해야 한다. refresh 재계산이 비활성화한 문서의 URL이
        # effective_map에서 빠지면 매일 "신규"로 오인해 재크롤링 루프가 생긴다
        # (2027년 적용 최저임금 고시 사례).
        if url_list:
            # 당일 발견된 URL만 조회 - 전체 스캔 타임아웃 방지
            query = {"metadata.source_url": {"$in": url_list}}
        else:
            query = {"metadata.source_url": {"$exists": True}}

        docs = collection.find(
            query,
            {"metadata.source_url": 1, "metadata.effective": 1, "metadata.is_active": 1,
             "metadata.save_incomplete": 1},
        ).max_time_ms(60000)

        # 활성 버전이 있으면 그 effective를 쓰고, 없으면 비활성 버전 중 최신 effective
        active_map: dict = {}
        inactive_map: dict = {}
        incomplete: set = set()
        for doc in docs:
            meta = doc.get("metadata", {})
            source_url = meta.get("source_url")
            if not source_url:
                continue
            effective = meta.get("effective") or ""
            if meta.get("is_active") and meta.get("save_incomplete"):
                incomplete.add(source_url)
            target = active_map if meta.get("is_active") else inactive_map
            if effective >= target.get(source_url, ""):
                target[source_url] = effective

        effective_map = {**inactive_map, **active_map}
        # 조문 일부만 저장된 문서는 시행일자와 상관없이 다시 수집한다(filter_new_urls 가 None 을 재수집으로 본다).
        for url in incomplete:
            effective_map[url] = None
        return effective_map

    except Exception as e:
        # 빈 매핑으로 넘기면 전체를 신규로 보고 대량 재수집한다 → 스크래퍼 실패로 올린다.
        raise RuntimeError(f"MongoDB effective 조회 실패: {e}") from e


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
            elif effective_map[url_str] is None:
                new_items.append(item)  # 지난 실행에서 조문 일부만 저장됨 → 다시 수집
            else:
                stored_effective = (effective_map[url_str] or "").replace("-", "")
                item_effective = (item.get("effective", "") if isinstance(item, dict) else "").replace("-", "")
                # 99991231 = 시행일 미정/계속시행 sentinel → 날짜 비교 무의미, 이미 DB에 있으면 skip
                if item_effective == "99991231":
                    skipped += 1
                elif stored_effective != item_effective:
                    new_items.append(item)  # 개정됨
                else:
                    skipped += 1
        else:
            # URL 직접 매칭 또는 URL 쿼리 파라미터에서 doc_id 추출 후 매칭
            # (precSeq, detcSeq, expcSeq, deccSeq, jgmtSn 등 → distinct doc_id와 비교)
            if url_str in crawled_urls:
                skipped += 1
            else:
                m = re.search(r'(?:Seq|Sn)=(\d+)', url_str or '')
                if m and m.group(1) in crawled_urls:
                    skipped += 1
                else:
                    new_items.append(item)

    if skipped > 0:
        print(f"🔍 update 모드: {skipped}개 기존 URL 건너뜀, {len(new_items)}개 신규/변경 크롤링 예정")
    return new_items


# ============================================================================
# 단일 스크래퍼 실행
# ============================================================================

def summarize_scrape_results(results) -> tuple[int, int, int]:
    """scrape_with_semaphore 결과 목록을 (성공, 변경없음, 실패) 건수로 집계한다.

    action == "no_change" 인 경우만 변경없음으로 집계하고, 그 외 성공
    (new_version/update_existing/insert 등)은 모두 성공으로 집계한다.
    """
    success_count = sum(
        1 for r in results
        if isinstance(r, dict) and r.get("status") == "success" and r.get("action") != "no_change"
    )
    no_change_count = sum(
        1 for r in results
        if isinstance(r, dict) and r.get("status") == "success" and r.get("action") == "no_change"
    )
    failed_count = sum(1 for r in results if not is_success_result(r))
    return success_count, no_change_count, failed_count


def is_success_result(result) -> bool:
    return isinstance(result, dict) and result.get("status") == "success"


def failed_doc_target(url_item, result) -> str:
    """실패한 문서를 배치 보고용 대상 id 로 만든다(doc_id → URL 의 Seq/Sn → URL)."""
    doc_id = result.get("doc_id") if isinstance(result, dict) else None
    if doc_id:
        return f"doc:{doc_id}"
    url_str = url_item.get("url") or url_item.get("doc_seq") if isinstance(url_item, dict) else url_item
    m = re.search(r'(?:Seq|Sn)=(\d+)', str(url_str or ''))
    return f"doc:{m.group(1)}" if m else f"url:{url_str}"


async def run_single_scraper(
    scraper_type: str,
    max_concurrent: int,
    max_pages: Optional[int],
    mode: str = "full",
) -> Dict:
    """
    단일 scraper 실행

    Args:
        scraper_type: 스크래퍼 타입
        max_concurrent: 동시 처리 수
        max_pages: 최대 페이지 수 (None = 무제한)
        mode: "full" (전체) 또는 "update" (증분)
    """
    config = get_scraper_config(scraper_type)
    run_report = start_run_report(scraper_type)

    print(f"\n{'='*80}")
    print(f"🚀 {config.display_name} 크롤링 시작 (모드: {mode}, 동시 처리: {max_concurrent})")
    print(f"{'='*80}\n")

    total_discovered = 0
    try:
        list_scraper_module = import_module(config.list_module)
        scraper_module = import_module(config.scraper_module)

        fetch_urls = getattr(list_scraper_module, "fetch_urls")
        scrape_and_save = getattr(scraper_module, "scrape_and_save")

        list_page_url = config.resolve_list_page_url()

        # Step 1: URL 수집
        print("📍 Step 1: URL 수집 시작...")
        try:
            urls = await fetch_urls(
                start_url=list_page_url,
                max_pages_arg=max_pages
            )
        except Exception as e:
            # 목록 수집 중 예외는 외부 수집원 일시 장애로 본다(재시도 대상). 목록 밖의 오류와 구분한다.
            print(f"❌ 목록 수집 오류: {e}")
            import traceback
            traceback.print_exc()
            return {
                "status": "failed",
                "error": f"목록 수집 실패: {e}",
                "total_urls": 0,
                "total_success": 0,
                "total_failed": 0,
                "failed_targets": [f"{scraper_type}:listing"],
            }
        print(f"✅ Step 1 완료: {len(urls)}개의 URL 발견\n")

        if not urls:
            return {
                "status": "failed",
                "error": "No URLs found",
                "total_urls": 0,
                "total_success": 0,
                "total_failed": 0,
                "failed_targets": [f"{scraper_type}:listing"],
            }
        listing_failed_targets = list(run_report.failed_targets)
        if listing_failed_targets:
            print(f"⚠️  목록 일부 실패 {len(listing_failed_targets)}건: {', '.join(listing_failed_targets[:10])}")

        # Step 1.5 (update 모드): 이미 크롤링된 URL 제거
        total_discovered = len(urls)
        skipped_count = 0
        if mode == "update":
            # 필터 방식은 컬렉션 설정(config.update_strategy)이 정한다.
            # - 시행일자(law/adrule): 개정 여부를 정확히 판별한다.
            # - doc_id(나머지): 이미 있는 문서는 건너뛴다(내용 불변). 재수집 대상은 설정의 retry 필터가 뺀다.
            if config.update_strategy == UPDATE_BY_EFFECTIVE_DATE:
                print("📍 Step 1.5: update 모드 — 시행일자 기반 변경 감지 중...")
                url_str_list = [
                    (item.get("url") if isinstance(item, dict) else item)
                    for item in urls if item
                ]
                effective_map = get_crawled_effective_dates(config.collection_name, url_list=url_str_list)
                urls = filter_new_urls(urls, set(), effective_map=effective_map)
            elif config.update_strategy == UPDATE_BY_DOC_ID:
                print("📍 Step 1.5: update 모드 — 기존 URL/ID 존재 여부로 신규 항목만 필터링 중...")
                crawled_urls = get_all_crawled_urls(config.collection_name, url_items=urls)
                urls = filter_new_urls(urls, crawled_urls)
            else:
                raise ValueError(f"update 모드 필터가 정의되지 않은 스크래퍼: {scraper_type}")
            skipped_count = total_discovered - len(urls)
            print(f"✅ Step 1.5 완료: {total_discovered}개 중 {len(urls)}개 신규/변경 크롤링 대상\n")

            if not urls:
                print("ℹ️  신규/변경 항목이 없습니다. 건너뜁니다.")
                return {
                    "status": "success",
                    "total_urls": total_discovered,
                    "total_success": 0,
                    "total_failed": len(listing_failed_targets),
                    "skipped": skipped_count,
                    "failed_targets": listing_failed_targets,
                    "failure_reasons": dict(run_report.reason_counts),
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
                    if not result or result.get("status") in ("failed", "error"):
                        error = (result or {}).get("error") or "scrape_and_save returned None or failed"
                        note_document_failure(error)
                        return {
                            "status": "failed",
                            "error": error,
                            "target": failed_doc_target(url_item, result),
                        }
                    action = result.get("action", "insert")
                    return {"status": "success", "doc_id": result.get("doc_id"), "action": action}
                except Exception as e:
                    note_document_failure(e)
                    return {"status": "failed", "error": str(e), "target": failed_doc_target(url_item, None)}

        tasks = [scrape_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count, no_change_count, failed_count = summarize_scrape_results(results)
        doc_failed_targets = [
            f"{scraper_type}:{r['target']}" if isinstance(r, dict) and r.get("target")
            else f"{scraper_type}:doc:unknown"
            for r in results if not is_success_result(r)
        ]

        print(f"\n✅ Step 2 완료: {success_count}개 성공, {no_change_count}개 변경없음, {failed_count}개 실패\n")

        return {
            "status": "success" if success_count + no_change_count > 0 else "failed",
            "total_urls": total_discovered,
            "total_success": success_count,
            "total_no_change": no_change_count,
            "total_failed": failed_count + len(listing_failed_targets),
            "skipped": skipped_count,
            "failed_targets": listing_failed_targets + doc_failed_targets,
            "failure_reasons": dict(run_report.reason_counts),
        }

    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        # 목록 수집 밖의 오류(DB 조회·import·코드 오류)는 목록 실패가 아니다 → 종료코드 2(재시도)로 보내지 않는다.
        return unexpected_error_result(scraper_type, e, total_discovered, dict(run_report.reason_counts))


# 스크래퍼 시작 간격(초). 00:00:00에 10개가 한꺼번에 law.go.kr에 붙으면
# 연결이 끊겨(RemoteDisconnected) 목록 수집이 실패하던 문제를 줄인다.
SCRAPER_START_INTERVAL_SECONDS = float(os.getenv("SCRAPER_START_INTERVAL_SECONDS", "3"))


def _run_scraper_in_thread(args):
    """스레드 내에서 단일 스크래퍼 실행 (자체 이벤트 루프)"""
    scraper_type, max_concurrent, max_pages, mode, start_delay = args
    if start_delay:
        time.sleep(start_delay)
    return asyncio.run(run_single_scraper(scraper_type, max_concurrent, max_pages, mode))

async def run_multiple_scrapers(scrapers, max_concurrent, max_pages, mode):
    """여러 scraper 병렬 실행 (스레드풀 — 동기 I/O 블로킹 우회)"""
    import concurrent.futures
    loop = asyncio.get_event_loop()
    args_list = [
        (s, max_concurrent, max_pages, mode, index * SCRAPER_START_INTERVAL_SECONDS)
        for index, s in enumerate(scrapers)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(scrapers)) as pool:
        futures = [loop.run_in_executor(pool, _run_scraper_in_thread, args) for args in args_list]
        task_results = await asyncio.gather(*futures, return_exceptions=True)
    results = {}
    for scraper_type, result in zip(scrapers, task_results):
        if isinstance(result, Exception):
            results[scraper_type] = unexpected_error_result(scraper_type, result)
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
        print(f"   - 변경없음: {result.get('total_no_change', 0):,}개")
        print(f"   - 실패: {result.get('total_failed', 0):,}개")
        if result.get("skipped"):
            print(f"   - 건너뜀: {result.get('skipped', 0):,}개 (이미 크롤링됨)")

        total_urls += result.get('total_urls', 0)
        total_success += result.get('total_success', 0)
        total_failed += result.get('total_failed', 0)

    print(f"\n{'='*80}")
    print("📈 전체 통계")
    print(f"{'='*80}")
    print(f"   - 전체 발견 URL: {total_urls:,}개")
    print(f"   - 전체 성공: {total_success:,}개")
    print(f"   - 전체 실패: {total_failed:,}개")
    if total_urls > 0:
        print(f"   - 성공률: {total_success / total_urls * 100:.1f}%")
    print("\n💾 저장 위치: MongoDB (original_db)")
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

  # 증분 업데이트 (DB에 있는 문서는 건너뜀)
  python3 crawl.py --mode update --scraper case

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
            '  update: 증분 업데이트 (DB에 있는 문서 건너뜀)'
        )
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='사용 가능한 scraper 목록 표시'
    )
    parser.add_argument(
        '--result-json',
        default=None,
        help='스크래퍼별 결과를 JSON 파일로 남길 경로 (run_daily.sh 의 배치 결과 보고용)'
    )

    return parser.parse_args()


def write_result_json(path: Optional[str], results: Dict[str, dict], exit_code: int) -> None:
    """스크래퍼별 결과를 파일로 남긴다. 로그 문구를 파싱하지 않고 결과를 집계하기 위함."""
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"exit_code": exit_code, "scrapers": results}, f, ensure_ascii=False, default=str)


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

    print("\n🔧 크롤링 설정:")
    print(f"   - 모드: {args.mode}")
    print(f"   - 동시 수: {args.concurrent}")
    print(f"   - 최대 페이지: {args.pages or '무제한'}")
    if args.mode == "update":
        print("   - 건너뜀 기준: DB에 이미 있는 문서 (law/adrule은 시행일자가 같은 문서)")

    start_time = datetime.now()
    print(f"\n⏰ 시작 시간: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)

    try:
        if len(scrapers) == 1:
            result = asyncio.run(run_single_scraper(
                scrapers[0], args.concurrent, args.pages, args.mode
            ))
            results = {scrapers[0]: result}
        else:
            results = asyncio.run(run_multiple_scrapers(
                scrapers, args.concurrent, args.pages, args.mode
            ))

        print_results(results)

        end_time = datetime.now()
        elapsed = (end_time - start_time).total_seconds() / 60

        print(f"🏁 종료 시간: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⏱️  소요 시간: {elapsed:.1f}분 ({int(elapsed // 60)}시간 {int(elapsed % 60)}분)")
        print()

        exit_code = compute_exit_code(results)
        if exit_code == EXIT_LISTING_FAILURE:
            print("⚠️  일부 스크래퍼가 URL 목록 수집에 실패했습니다 (외부 수집원 일시 장애 가능성).")
        write_result_json(args.result_json, results, exit_code)
        return exit_code

    except KeyboardInterrupt:
        print("\n\n⚠️  사용자가 중단했습니다.")
        return EXIT_FATAL

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return EXIT_FATAL


if __name__ == '__main__':
    sys.exit(main())
