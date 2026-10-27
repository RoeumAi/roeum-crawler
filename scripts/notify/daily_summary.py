#!/usr/bin/env python3
"""일일 크롤링 결과를 Discord 로 보고한다(공통 형식 alert_format).

run_daily.sh 가 `python -m scripts.notify.daily_summary` 로 부른다. 시도마다 남긴 crawl.py --result-json 파일을 읽는다. 배치 관리 보고(BATCH_RESULT)와
같은 데이터라서 두 보고의 숫자가 어긋나지 않는다. 메시지 생성(build_message)은 순수 함수라
tests/test_daily_summary_message.py 로 검증한다.
"""
import argparse
import os

from scripts.core.config import SCRAPERS
from scripts.notify.alert_format import format_alert, get_env_name, resolve_webhook, send_discord
from scripts.report.batch_result import (
    EXIT_LISTING_FAILURE, EXIT_OK, is_listing_failure, load_attempt, merge_attempts,
)

# 로그 위치 안내. ECS 는 태스크 정의에서 CloudWatch 위치로 바꾼다.
DEFAULT_LOG_LOCATION = "맥미니 loum@100.93.93.51 의 ~/loum/roeum-crawler/logs/daily_*.log"


def _n(result: dict, key: str) -> int:
    return int(result.get(key) or 0)


def describe_scraper(name: str, result: dict | None) -> tuple[str, str]:
    """스크래퍼 한 줄과 상태(ok·listing·partial·missing)."""
    if result is None:
        return f"⬜ {name}: 미실행", "missing"
    if is_listing_failure(result):
        return f"⚠️ {name}: 수집원 일시 오류 (URL 목록 수집 실패)", "listing"
    success, no_change = _n(result, "total_success"), _n(result, "total_no_change")
    skipped, failed = _n(result, "skipped"), _n(result, "total_failed")
    if failed:
        return f"❌ {name}: 실패 {failed:,}건 (업데이트 {success:,} / 변경없음 {no_change:,})", "partial"
    if success:
        return f"✅ {name}: {success:,}건 업데이트 (변경없음 {no_change:,}건 / 건너뜀 {skipped:,}건)", "ok"
    if no_change:
        return f"✅ {name}: 변경 없음 (재확인 {no_change:,}건 / 건너뜀 {skipped:,}건)", "ok"
    return f"✅ {name}: 변경 없음 (건너뜀 {skipped:,}건)", "ok"


def build_message(
    scrapers: dict,
    *,
    duration_min: int,
    attempts: int,
    date_str: str,
    missing_attempt: bool = False,
    exit_code: int = 0,
    refresh_exit_code: int = 0,
    target: str = "roeum-crawler(daily)",
    env: str = "prod",
    log_location: str = DEFAULT_LOG_LOCATION,
) -> str:
    """심각도: 크롤러·재계산 비정상 종료나 문서 실패 → ERROR, 외부 수집원 목록 실패만 → WARN, 그 외 INFO."""
    hours, mins = divmod(duration_min, 60)
    duration_str = f"{hours}시간 {mins}분" if hours else f"{mins}분"
    total_updated = sum(_n(r, "total_success") for r in scrapers.values())
    total_no_change = sum(_n(r, "total_no_change") for r in scrapers.values())

    details = [f"실행 시각: {date_str}"]
    listing, partial = [], []
    for key, config in SCRAPERS.items():
        line, state = describe_scraper(config.display_name, scrapers.get(key))
        details.append(line)
        if state == "listing":
            listing.append(config.display_name)
        elif state == "partial":
            partial.append(config.display_name)

    if attempts > 1:
        details.append(f"🔁 외부 수집원 오류로 {attempts - 1}회 재시도함")

    errors = []
    if missing_attempt or exit_code not in (EXIT_OK, EXIT_LISTING_FAILURE):
        errors.append(f"크롤러 비정상 종료(코드 {exit_code})")
    if refresh_exit_code != 0:
        errors.append(f"현재 시행 버전 재계산 실패(코드 {refresh_exit_code})")
    if partial:
        errors.append(f"{', '.join(partial)} 일부 문서 실패")

    totals = f"전체 업데이트 {total_updated:,}건 (변경없음 {total_no_change:,}건) · 소요 {duration_str}"
    if errors:
        severity, kind = "ERROR", "데이터 수집"
        summary = "; ".join(errors) + " — 로그 확인 필요"
    elif listing:
        severity, kind = "WARN", "데이터 수집"
        summary = (f"law.go.kr 등 외부 수집원 일시 오류로 {', '.join(listing)} 미수집 "
                   f"— 데이터 유실 아님(다음 실행 시 자동 반영)")
    else:
        severity, kind = "INFO", "보고"
        summary = totals
    if severity != "INFO":
        details.append(f"🔢 {totals}")

    return format_alert(
        severity, kind, target, summary, env,
        trace=f"실행 {date_str}",
        search=f"{log_location} (grep -E '❌|Traceback')",
        details=details,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="크롤러 실행 요약 Discord 알림 전송")
    parser.add_argument('--attempt-json', action='append', default=[], help='시도별 crawl.py --result-json 파일')
    parser.add_argument('--duration', type=int, default=0, help='소요 시간(분)')
    parser.add_argument('--attempts', type=int, default=1, help='크롤링 시도 횟수 (재시도 포함)')
    parser.add_argument('--date', default='', help='표시용 날짜 문자열')
    parser.add_argument('--target', default='roeum-crawler(daily)', help='알림 대상 표기')
    parser.add_argument('--env', default=None, help='prod | staging (기본 ROEUM_ENV 또는 prod)')
    parser.add_argument('--exit-code', type=int, default=0, help='마지막 crawl.py 종료코드')
    parser.add_argument('--refresh-exit-code', type=int, default=0, help='버전 재계산 종료코드')
    args = parser.parse_args(argv)

    scrapers, missing = merge_attempts([load_attempt(path) for path in args.attempt_json])
    msg = build_message(
        scrapers,
        duration_min=args.duration, attempts=args.attempts, date_str=args.date,
        missing_attempt=missing or not args.attempt_json,
        exit_code=args.exit_code, refresh_exit_code=args.refresh_exit_code,
        target=args.target, env=args.env or get_env_name(),
        log_location=os.environ.get("CRAWLER_LOG_LOCATION") or DEFAULT_LOG_LOCATION,
    )

    # 웹훅은 argv(ps 노출)가 아닌 환경변수로만 받는다: DISCORD_WEBHOOK_DATA_COLLECT → DISCORD_WEBHOOK
    url = resolve_webhook('data_collect')
    if not url:
        print("Discord 알림 건너뜀: 웹훅 환경변수 없음")
        return
    try:
        send_discord(url, msg)
        print("Discord 알림 전송 완료")
    except Exception as e:  # noqa: BLE001 - 알림 실패가 크롤링을 막지 않도록 (URL 노출 방지로 타입만 출력)
        print(f"Discord 알림 전송 실패: {type(e).__name__}")


if __name__ == '__main__':
    main()
