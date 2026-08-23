#!/usr/bin/env python3
"""일일 크롤러 실행 로그를 파싱해 Discord 알림 메시지를 만들고 전송한다.

run_daily.sh 의 인라인 heredoc 를 대체한다. 메시지 생성 로직(build_message)은
순수 함수라 tests/test_daily_summary_message.py 로 검증한다.
"""
import argparse
import json
import re
import urllib.request


SCRAPERS = [
    'law', 'adrule', 'case', 'decision', 'interpretation',
    'mediation_case', 'judgment', 'constitutional_decc', 'legislation_expc', 'admin_decc',
]

NAMES = {
    'law': '법령', 'adrule': '행정규칙', 'case': '판례',
    'decision': '심의결정례', 'interpretation': '해석례',
    'mediation_case': '조정사건례', 'judgment': '주요판정사례',
    'constitutional_decc': '헌재결정례', 'legislation_expc': '법제처해석례', 'admin_decc': '행정심판재결례',
}


def _pnum(pattern, text):
    m = re.search(pattern + r'[^0-9]*([\d,]+)', text)
    return int(m.group(1).replace(',', '')) if m else None


def parse_scraper_results(log_content: str) -> dict:
    """크롤링 최종 결과 섹션을 파싱한다.

    로그에 여러 번의 시도가 append 된 경우(재시도), 각 스크래퍼의 마지막 결과 블록이
    최종 상태를 나타내므로 뒤에 나오는 값이 앞의 값을 덮어쓴다.
    """
    results = {}
    current = None
    for line in log_content.split('\n'):
        if '======' in line or '📈 전체 통계' in line:
            current = None  # 전체 통계 섹션 진입 시 스크래퍼별 파싱 중단
            continue
        for scraper in SCRAPERS:
            if f'({scraper})' in line and ('✅' in line or '❌' in line):
                current = scraper
                results[scraper] = {
                    'status': '✅' if '✅' in line else '❌',
                    'success': 0, 'skipped': 0, 'failed': 0, 'total': 0, 'no_change': 0,
                }
                break
        if current:
            v = _pnum(r'발견 URL', line)
            if v is not None:
                results[current]['total'] = v
            v = _pnum(r'성공', line)
            if v is not None:
                results[current]['success'] = v
            v = _pnum(r'실패', line)
            if v is not None:
                results[current]['failed'] = v
            v = _pnum(r'건너뜀', line)
            if v is not None:
                results[current]['skipped'] = v
            v = _pnum(r'변경없음', line)
            if v is not None:
                results[current]['no_change'] = v
    return results


def build_message(log_content: str, duration_min: int, attempts: int, date_str: str) -> str:
    """로그 내용으로 Discord 알림 메시지 문자열을 만든다."""
    results = parse_scraper_results(log_content)

    total_updated = sum(r.get('success', 0) for r in results.values())
    total_no_change = sum(r.get('no_change', 0) for r in results.values())

    hours = duration_min // 60
    mins = duration_min % 60
    duration_str = f"{hours}시간 {mins}분" if hours > 0 else f"{mins}분"

    out = [f"📊 **일일 크롤러 업데이트** ({date_str})\n"]

    listing_failures = []  # URL 목록조차 못 모은 스크래퍼 (외부 수집원 장애 신호)
    other_failures = []    # 목록은 모았으나 실패 상태인 스크래퍼

    for scraper in SCRAPERS:
        r = results.get(scraper)
        name = NAMES[scraper]
        if not r:
            out.append(f"⬜ {name}: 미실행")
        elif r['status'] == '❌':
            if r.get('total', 0) == 0:
                # law.go.kr 404 처럼 목록 수집 자체가 실패 → 외부 수집원 문제로 명시
                listing_failures.append(name)
                out.append(f"⚠️ {name}: 수집원 일시 오류 (URL 목록 수집 실패)")
            else:
                other_failures.append(name)
                out.append(f"❌ {name}: 일부 문서 처리 실패 (실패 {r.get('failed', 0):,}건)")
        else:
            updated = r['success']
            skipped = r['skipped']
            no_change = r.get('no_change', 0)
            if updated > 0:
                out.append(f"✅ {name}: **{updated:,}건 업데이트** (변경없음 {no_change:,}건 / 건너뜀 {skipped:,}건)")
            elif no_change > 0:
                out.append(f"✅ {name}: 변경 없음 (재확인 {no_change:,}건 / 건너뜀 {skipped:,}건)")
            else:
                out.append(f"✅ {name}: 변경 없음 (건너뜀 {skipped:,}건)")

    out.append(
        f"\n🔢 전체 업데이트: **{total_updated:,}건** (변경없음 {total_no_change:,}건)  "
        f"⏱️ 소요시간: {duration_str}"
    )

    retries = max(0, attempts - 1)
    if retries > 0:
        out.append(f"🔁 외부 수집원 오류로 {retries}회 재시도함")

    if listing_failures:
        out.append(
            f"⚠️ law.go.kr 등 외부 수집원 일시 오류로 {', '.join(listing_failures)} 미수집 "
            f"— 데이터 유실 아님(다음 실행 시 자동 반영)"
        )
    if other_failures:
        out.append(f"❗ {', '.join(other_failures)} 일부 문서 실패 — 로그 확인 필요")

    return '\n'.join(out)


def send_discord(webhook_url: str, message: str) -> None:
    payload = json.dumps({"content": message}).encode('utf-8')
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={'Content-Type': 'application/json', 'User-Agent': 'DiscordBot (private, 1.0)'},
        method='POST',
    )
    urllib.request.urlopen(req)


def main():
    parser = argparse.ArgumentParser(description="일일 크롤러 Discord 알림 전송")
    parser.add_argument('--log', required=True, help='daily 로그 파일 경로')
    parser.add_argument('--webhook', required=True, help='Discord webhook URL')
    parser.add_argument('--duration', type=int, default=0, help='소요 시간(분)')
    parser.add_argument('--attempts', type=int, default=1, help='크롤링 시도 횟수 (재시도 포함)')
    parser.add_argument('--date', default='', help='표시용 날짜 문자열')
    args = parser.parse_args()

    with open(args.log, 'r', encoding='utf-8') as f:
        content = f.read()

    msg = build_message(content, args.duration, args.attempts, args.date)

    try:
        send_discord(args.webhook, msg)
        print("Discord 알림 전송 완료")
    except Exception as e:  # noqa: BLE001 - 알림 실패가 크롤링을 막지 않도록
        print(f"Discord 알림 전송 실패: {e}")


if __name__ == '__main__':
    main()
