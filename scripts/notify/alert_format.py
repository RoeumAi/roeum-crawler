#!/usr/bin/env python3
"""Discord 알림 공통 형식 (서버 알림 형식 S1 초안과 동일).

    [심각도] 종류 · 대상          예) [ERROR] 데이터 수집 · roeum-crawler(daily)
    요약 한 줄
    - 환경: prod | staging
    - 추적: <추적 ID>             (크롤러: 실행 로그 경로)
    - 검색: 로그 위치 안내
    - 세부: 최대 N줄

※ infra/lambda/roum-discord-notifier/index.py 의 format_alert 와 같은 형식이다.
  한쪽을 바꾸면 다른 쪽도 같이 바꾼다 (Lambda 는 단일 zip 이라 모듈 공유 불가).

채널(서버 오류·배치·배포·데이터 수집)은 환경변수 '이름'만 정의한다. 웹훅 URL 매핑이
확정되기 전에는 값이 비어 있어 기존 DISCORD_WEBHOOK 으로 폴백 → 수신 채널 불변.
웹훅 URL 은 출력·로그에 남기지 않는다.

CLI (셸 스크립트용):
    python alert_format.py --channel data_collect --severity WARN --kind 배치 \
        --target "roeum-crawler(daily)" --summary "..." --trace "..." --search "..."
"""
import argparse
import json
import os
import urllib.request

SEVERITIES = ("CRITICAL", "ERROR", "WARN", "INFO")
KINDS = ("서버 오류", "배치", "배포", "데이터 수집", "외부 연동", "보고")
MAX_DETAIL_LINES = 15
DISCORD_CONTENT_LIMIT = 1900  # Discord 2000자 제한 여유

# 채널 키 → 웹훅 URL 을 담는 환경변수 이름 (.env 에도 같은 이름으로 둔다)
CHANNEL_ENV = {
    "server_error": "DISCORD_WEBHOOK_SERVER_ERROR",   # 서버 오류
    "batch": "DISCORD_WEBHOOK_BATCH",                 # 배치
    "deploy": "DISCORD_WEBHOOK_DEPLOY",               # 배포
    "data_collect": "DISCORD_WEBHOOK_DATA_COLLECT",   # 데이터 수집
}
DEFAULT_WEBHOOK_ENV = "DISCORD_WEBHOOK"  # 기존 웹훅 (채널별 값이 없을 때 기본값)


def get_env_name() -> str:
    """환경 표기. ROEUM_ENV(prod|staging), 기본 prod (맥미니 크롤러는 운영 DB 대상)."""
    return os.environ.get("ROEUM_ENV", "prod") or "prod"


def format_alert(severity, kind, target, summary, env, trace, search, details=(),
                 max_detail_lines=MAX_DETAIL_LINES) -> str:
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity: {severity}")
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind}")

    def one(v):  # 줄바꿈 제거 → 한 줄 보장
        return " ".join(str(v).split())

    lines = [
        f"[{severity}] {kind} · {one(target)}",
        one(summary),
        f"- 환경: {env}",
        f"- 추적: {one(trace)}",
        f"- 검색: {one(search)}",
    ]
    detail_lines = [str(d).rstrip() for d in details if str(d).strip()]
    if detail_lines:
        shown = detail_lines[:max_detail_lines]
        lines.append("- 세부:")
        lines.extend(f"  {d}" for d in shown)
        if len(detail_lines) > len(shown):
            lines.append(f"  … 외 {len(detail_lines) - len(shown)}줄")
    text = "\n".join(lines)
    if len(text) > DISCORD_CONTENT_LIMIT:
        text = text[: DISCORD_CONTENT_LIMIT - 1] + "…"
    return text


def resolve_webhook(channel, explicit=None, environ=None) -> str:
    """우선순위: 명시 인자 → 채널 환경변수 → 기존 DISCORD_WEBHOOK."""
    environ = os.environ if environ is None else environ
    if explicit:
        return explicit
    return (environ.get(CHANNEL_ENV.get(channel, ""), "")
            or environ.get(DEFAULT_WEBHOOK_ENV, ""))


def send_discord(webhook_url: str, message: str) -> None:
    send_discord_payload(webhook_url, {"content": message})


def send_discord_payload(webhook_url: str, payload: dict) -> None:
    """웹훅 payload(content·embeds) 그대로 보낸다. 2xx 가 아니면 HTTPError."""
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "DiscordBot (private, 1.0)"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15)


def main():
    p = argparse.ArgumentParser(description="공통 형식 Discord 알림 전송")
    p.add_argument("--channel", default="data_collect", choices=sorted(CHANNEL_ENV))
    p.add_argument("--severity", required=True, choices=SEVERITIES)
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--target", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--trace", required=True)
    p.add_argument("--search", required=True)
    p.add_argument("--detail", action="append", default=[])
    p.add_argument("--env", default=None)
    args = p.parse_args()
    msg = format_alert(args.severity, args.kind, args.target, args.summary,
                       args.env or get_env_name(), args.trace, args.search, args.detail)
    url = resolve_webhook(args.channel)
    if not url:
        print("Discord 알림 건너뜀: 웹훅 환경변수 없음")
        return
    try:
        send_discord(url, msg)
        print("Discord 알림 전송 완료")
    except Exception as e:  # noqa: BLE001 - 알림 실패가 크롤링을 막지 않도록
        print(f"Discord 알림 전송 실패: {type(e).__name__}")


if __name__ == "__main__":
    main()
