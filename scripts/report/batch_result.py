#!/usr/bin/env python3
"""일일 크롤링 결과를 배치 관리 시스템 보고 형식으로 합친다.

run_daily.sh 가 시도마다 crawl.py --result-json 으로 남긴 파일과 재계산 종료코드를 받아
표준 출력 마지막 줄에 `BATCH_RESULT {...}` 를 찍고, 작업의 종료코드를 돌려준다.
형식은 배치 관리 설계(2026-10-25) 4-3: processed·failed·failedTargets·summary.

종료코드
- 0: 끝까지 돌았다. 일부 문서·목록 페이지 실패는 failed 로 보고(→ 부분 실패)
- 2: 재시도 뒤에도 목록 전체를 못 모은 스크래퍼가 있다
- 1: 크롤러 비정상 종료, 결과 파일 없음, 현재 시행 버전 재계산 실패

입력과 무관한 순수 함수(merge_attempts, build_batch_result)는 tests/test_batch_result.py 로 검증한다.
"""
import argparse
import json
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_LISTING_FAILURE = 2
MAX_FAILED_TARGETS = 50

NAMES = {
    'law': '법령', 'adrule': '행정규칙', 'case': '판례',
    'decision': '심의결정례', 'interpretation': '해석례',
    'mediation_case': '조정사건례', 'judgment': '주요판정사례',
    'constitutional_decc': '헌재결정례', 'legislation_expc': '법제처해석례', 'admin_decc': '행정심판재결례',
}


def load_attempt(path: str) -> dict | None:
    """결과 파일을 읽는다. crawl.py 가 쓰기 전에 죽었으면 None."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_listing_failure(result: dict) -> bool:
    return result.get("status") == "failed" and not result.get("total_urls")


def merge_attempts(attempts: list[dict | None]) -> tuple[dict, bool]:
    """시도별 결과를 스크래퍼마다 가장 마지막 결과로 합친다.

    2회차부터는 실패한 스크래퍼만 다시 돌기 때문에, 1회차에 성공한 스크래퍼 결과는 그대로 남는다.
    반환: (스크래퍼별 최종 결과, 결과 파일이 빠진 시도가 있었는지)
    """
    merged: dict = {}
    missing = False
    for attempt in attempts:
        if attempt is None:
            missing = True
            continue
        merged.update(attempt.get("scrapers") or {})
    return merged, missing


def retry_scrapers(attempt: dict | None) -> list[str]:
    """다음 시도에서 다시 돌릴 스크래퍼(목록 전체 실패)."""
    if not attempt:
        return []
    return [name for name, result in (attempt.get("scrapers") or {}).items() if is_listing_failure(result)]


def build_batch_result(
    scrapers: dict,
    *,
    missing_attempt: bool,
    last_crawl_exit_code: int,
    refresh_exit_code: int,
    attempts: int,
    duration_minutes: int,
) -> tuple[dict, int]:
    processed = 0
    failed = 0
    targets: list[str] = []
    failed_by_scraper: dict[str, int] = {}

    for name, result in scrapers.items():
        processed += int(result.get("total_success") or 0) + int(result.get("total_no_change") or 0)
        scraper_failed = int(result.get("total_failed") or 0)
        scraper_targets = list(result.get("failed_targets") or [])
        if is_listing_failure(result):
            # 목록 자체를 못 모은 스크래퍼는 실패 1건으로 센다(문서 수를 알 수 없음).
            scraper_failed = max(scraper_failed, 1)
            scraper_targets = scraper_targets or [f"{name}:listing"]
        if scraper_failed:
            failed_by_scraper[name] = scraper_failed
        failed += scraper_failed
        targets.extend(scraper_targets)

    if missing_attempt or last_crawl_exit_code not in (EXIT_OK, EXIT_LISTING_FAILURE):
        failed += 1
        targets.append("crawl:run")
    if refresh_exit_code != 0:
        failed += 1
        targets.append("refresh:current-status")

    if missing_attempt or last_crawl_exit_code not in (EXIT_OK, EXIT_LISTING_FAILURE) or refresh_exit_code != 0:
        exit_code = EXIT_FATAL
    elif any(is_listing_failure(result) for result in scrapers.values()):
        exit_code = EXIT_LISTING_FAILURE
    else:
        exit_code = EXIT_OK

    parts = [f"처리 {processed:,}", f"실패 {failed:,}"]
    detail = [f"{NAMES.get(name, name)} {count}" for name, count in failed_by_scraper.items()]
    if "crawl:run" in targets:
        detail.append("크롤러 비정상 종료")
    if "refresh:current-status" in targets:
        detail.append("시행 버전 재계산 실패")
    summary = " · ".join(parts)
    if detail:
        summary += f" ({', '.join(detail)})"
    summary += f" · 시도 {attempts}회 · {duration_minutes}분"

    return {
        "processed": processed,
        "failed": failed,
        "failedTargets": targets[:MAX_FAILED_TARGETS],
        "summary": summary,
    }, exit_code


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    retry = sub.add_parser("retry-scrapers", help="다음 시도에서 다시 돌릴 스크래퍼 이름을 공백으로 출력")
    retry.add_argument("attempt_json")

    final = sub.add_parser("final", help="BATCH_RESULT 줄을 찍고 종료코드를 돌려준다")
    final.add_argument("--attempt-json", action="append", default=[])
    final.add_argument("--last-crawl-exit-code", type=int, required=True)
    final.add_argument("--refresh-exit-code", type=int, required=True)
    final.add_argument("--attempts", type=int, required=True)
    final.add_argument("--duration-minutes", type=int, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.command == "retry-scrapers":
        print(" ".join(retry_scrapers(load_attempt(args.attempt_json))))
        return 0

    scrapers, missing = merge_attempts([load_attempt(path) for path in args.attempt_json])
    result, exit_code = build_batch_result(
        scrapers,
        missing_attempt=missing or not args.attempt_json,
        last_crawl_exit_code=args.last_crawl_exit_code,
        refresh_exit_code=args.refresh_exit_code,
        attempts=args.attempts,
        duration_minutes=args.duration_minutes,
    )
    print("BATCH_RESULT " + json.dumps(result, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
