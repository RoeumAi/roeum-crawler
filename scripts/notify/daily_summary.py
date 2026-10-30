#!/usr/bin/env python3
"""일일 크롤링 결과를 Discord 로 보고한다(embed 한 장).

run_daily.sh 가 `python -m scripts.notify.daily_summary` 로 부른다. 시도마다 남긴 crawl.py --result-json 파일을 읽는다. 배치 관리 보고(BATCH_RESULT)와
같은 데이터라서 두 보고의 숫자가 어긋나지 않는다. 메시지 생성(build_message)은 순수 함수라
tests/test_daily_summary_message.py 로 검증한다.

보이는 순서(10-29 사용자 요청 — 예전 글은 '세부:' 아래 줄이 한 덩어리로 붙어 읽기 어려웠다):
    제목: 상태 한 줄(성공·일부 실패…)
    실행 시각·소요 시간
    자료 유형마다 한 줄 — 실패 → 업데이트 → 변경 없음 순
    실패가 있으면 원인별 건수와 로그 위치
    전체 합계
    꼬리말: 환경·대상·심각도 같은 기술 정보
"""
import argparse
import os

from scripts.core.config import SCRAPERS
from scripts.core.run_report import EXTRACT_SERVER_DOWN
from scripts.notify.alert_format import get_env_name, resolve_webhook, send_discord_payload
from scripts.report.batch_result import (
    EXIT_LISTING_FAILURE, EXIT_OK, is_listing_failure, load_attempt, merge_attempts,
)

# 로그 위치 안내. 환경변수 CRAWLER_LOG_LOCATION 으로 정한다(ECS 는 Dockerfile 이 CloudWatch 위치를 넣는다).
DEFAULT_LOG_LOCATION = "로그 위치 미설정(환경변수 CRAWLER_LOG_LOCATION)"
TITLE = "법률 자료 일일 수집"
COLORS = {"INFO": 0x2ECC71, "WARN": 0xE67E22, "ERROR": 0xE74C3C}
DESCRIPTION_LIMIT = 4000  # Discord embed description 4096자 제한 여유
OTHER_REASON = "기타 저장 실패"

# 줄 정렬 순서: 실패 → 수집원 오류 → 업데이트 → 변경 없음 → 미실행
_ORDER = {"partial": 0, "listing": 1, "updated": 2, "unchanged": 3, "missing": 4}


def _n(result: dict, key: str) -> int:
    return int(result.get(key) or 0)


def describe_scraper(name: str, result: dict | None) -> tuple[str, str]:
    """스크래퍼 한 줄과 상태(partial·listing·updated·unchanged·missing)."""
    if result is None:
        return f"⬜ {name}: 미실행", "missing"
    if is_listing_failure(result):
        return f"⚠️ {name}: 수집원 일시 오류 (목록을 못 받음)", "listing"
    success, no_change = _n(result, "total_success"), _n(result, "total_no_change")
    skipped, failed = _n(result, "skipped"), _n(result, "total_failed")
    if failed:
        return f"❌ {name}: 실패 {failed:,}건 (업데이트 {success:,} · 변경없음 {no_change:,})", "partial"
    if success:
        return f"✅ {name}: {success:,}건 업데이트 (변경없음 {no_change:,} · 건너뜀 {skipped:,})", "updated"
    if no_change:
        return f"✅ {name}: 변경 없음 (재확인 {no_change:,} · 건너뜀 {skipped:,})", "unchanged"
    return f"✅ {name}: 변경 없음 (건너뜀 {skipped:,})", "unchanged"


def failure_reasons(scrapers: dict) -> dict[str, int]:
    """원인별 실패 건수. 원인을 모르는 문서 실패는 '기타 저장 실패'로 센다."""
    totals: dict[str, int] = {}
    for result in scrapers.values():
        reasons = {k: int(v) for k, v in (result.get("failure_reasons") or {}).items() if int(v) > 0}
        for reason, count in reasons.items():
            totals[reason] = totals.get(reason, 0) + count
        unexplained = _n(result, "total_failed") - sum(reasons.values())
        if unexplained > 0:
            totals[OTHER_REASON] = totals.get(OTHER_REASON, 0) + unexplained
    return dict(sorted(totals.items(), key=lambda item: -item[1]))


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
) -> dict:
    """Discord 웹훅 payload(embed 한 장).

    심각도: 크롤러·재계산 비정상 종료나 문서 실패 → ERROR, 외부 수집원 목록 실패만 → WARN, 그 외 INFO.
    """
    hours, mins = divmod(duration_min, 60)
    duration_str = f"{hours}시간 {mins}분" if hours else f"{mins}분"
    total_updated = sum(_n(r, "total_success") for r in scrapers.values())
    total_no_change = sum(_n(r, "total_no_change") for r in scrapers.values())

    rows = []
    for index, (key, config) in enumerate(SCRAPERS.items()):
        line, state = describe_scraper(config.display_name, scrapers.get(key))
        rows.append((_ORDER[state], index, line, state, config.display_name))
    rows.sort()
    partial = [name for *_, state, name in rows if state == "partial"]
    listing = [name for *_, state, name in rows if state == "listing"]

    errors = []
    if missing_attempt or exit_code not in (EXIT_OK, EXIT_LISTING_FAILURE):
        errors.append(f"크롤러 비정상 종료(코드 {exit_code})")
    if refresh_exit_code != 0:
        errors.append(f"현재 시행 버전 재계산 실패(코드 {refresh_exit_code})")

    if errors:
        severity, status, icon = "ERROR", "실패", "🚨"
    elif partial:
        severity, status, icon = "ERROR", "일부 실패", "❌"
    elif listing:
        severity, status, icon = "WARN", "수집원 일시 오류", "⚠️"
    else:
        severity, status, icon = "INFO", "성공", "✅"

    when = f"🕛 {date_str} 실행 · {duration_str} 소요"
    if attempts > 1:
        when += f" · 수집원 오류로 {attempts - 1}회 재시도"
    lines = [when, ""]
    lines += [line for _, _, line, _, _ in rows]

    reasons = failure_reasons(scrapers)
    if errors or reasons or listing:
        lines += ["", "**실패 원인**"]
        lines += [f"• {e}" for e in errors]
        lines += [f"• {reason} {count:,}건" for reason, count in reasons.items()]
        if listing:
            lines.append(f"• law.go.kr 등 외부 수집원 일시 오류로 {', '.join(listing)} 목록 미수집 — 데이터 유실 아님")
        if EXTRACT_SERVER_DOWN in reasons:
            # 10-29·10-30: 서버 쪽(운영 chat_generation 메모리 초과 재시작)이 그대로면 다음 실행에서도 같은 문서가 실패했다.
            lines.append("추출 서버가 실행 중 멈췄습니다. 서버 쪽 원인이 남아 있으면 다음 실행에서도 같은 문서가 실패합니다.")
        if reasons or listing:
            lines.append("실패한 문서는 다음 실행에서 다시 시도합니다.")
        lines.append(f"로그: {log_location}")

    lines += ["", f"🔢 전체 업데이트 {total_updated:,}건 · 변경없음 {total_no_change:,}건"]

    description = "\n".join(lines)
    if len(description) > DESCRIPTION_LIMIT:
        description = description[: DESCRIPTION_LIMIT - 1] + "…"
    return {"embeds": [{
        "title": f"{icon} {TITLE} — {status}",
        "description": description,
        "color": COLORS[severity],
        "footer": {"text": f"[{severity}] 데이터 수집 · {target} · 환경 {env}"},
    }]}


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
    payload = build_message(
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
        send_discord_payload(url, payload)
        print("Discord 알림 전송 완료")
    except Exception as e:  # noqa: BLE001 - 알림 실패가 크롤링을 막지 않도록 (URL 노출 방지로 타입만 출력)
        print(f"Discord 알림 전송 실패: {type(e).__name__}")


if __name__ == '__main__':
    main()
