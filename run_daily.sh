#!/bin/bash
CRAWLER_DIR="/Users/loum/loum/roeum-crawler"
PYTHON="/Users/loum/miniconda3/envs/crawler/bin/python"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DATE_STR=$(date "+%Y-%m-%d %H:%M")
LOG_FILE="$CRAWLER_DIR/logs/daily_${TIMESTAMP}.log"
DISCORD_WEBHOOK=$(grep '^DISCORD_WEBHOOK=' "$CRAWLER_DIR/.env" | cut -d'=' -f2-)

# ── 이전 프로세스 중복 실행 방지 ──
LOCKFILE="/tmp/roeum_crawler.lock"
if [ -f "$LOCKFILE" ]; then
    PID=$(cat "$LOCKFILE")
    if kill -0 "$PID" 2>/dev/null; then
        MSG="⏸️ **크롤러 스킵** (${DATE_STR})\n이전 실행(PID=${PID})이 아직 진행 중입니다. 오늘 실행을 건너뜁니다."
        python3 -c "
import json, urllib.request
payload = json.dumps({'content': '${MSG}'}).encode()
req = urllib.request.Request('${DISCORD_WEBHOOK}', data=payload, headers={'Content-Type': 'application/json'}, method='POST')
try: urllib.request.urlopen(req)
except: pass
"
        exit 0
    fi
fi
echo $$ > "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

mkdir -p "$CRAWLER_DIR/logs"
cd "$CRAWLER_DIR"

START_TIME=$(date +%s)

# ── 외부 수집원(law.go.kr 등) 일시 장애 대비 전체 재시도 설정 ──
# crawl.py 는 URL 목록조차 못 모은 스크래퍼가 있으면 종료코드 2를 반환한다.
# 크롤링은 멱등(이미 크롤링된 URL 은 건너뜀)이라 전체 재시도 비용이 낮다.
MAX_ATTEMPTS=3            # 총 시도 횟수 (최초 1 + 재시도 2)
RETRY_WAIT_SECONDS=900   # 재시도 전 대기 (15분) — law.go.kr 새벽 점검 창을 넘기기 위함

echo "======================================" >> "$LOG_FILE"
echo "일일 업데이트 시작: $(date)" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"

ATTEMPT=1
while true; do
    echo "" >> "$LOG_FILE"
    echo "▶ 크롤링 시도 ${ATTEMPT}/${MAX_ATTEMPTS}: $(date)" >> "$LOG_FILE"
    "$PYTHON" -u crawl.py --mode update --since 1 --concurrent 3 >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?

    # 종료코드 2(=URL 목록 수집 실패)일 때만 재시도. 그 외(정상/치명)면 종료.
    if [ "$EXIT_CODE" -ne 2 ] || [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
        break
    fi

    echo "⚠️ 외부 수집원 오류로 URL 목록 수집 실패 — ${RETRY_WAIT_SECONDS}초 후 재시도" >> "$LOG_FILE"
    ATTEMPT=$((ATTEMPT + 1))
    sleep "$RETRY_WAIT_SECONDS"
done

echo "======================================" >> "$LOG_FILE"
echo "law/adrule 현재 시행 버전 재계산 시작: $(date)" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"
"$PYTHON" -u scripts/core/flows/refresh_current_status_flow.py >> "$LOG_FILE" 2>&1

END_TIME=$(date +%s)
DURATION=$(( (END_TIME - START_TIME) / 60 ))

echo "종료: $(date)" >> "$LOG_FILE"

# 알림 메시지 생성·전송은 테스트 가능한 파이썬 모듈로 위임 (scripts/notify/daily_summary.py)
"$PYTHON" -u scripts/notify/daily_summary.py \
    --log "$LOG_FILE" \
    --webhook "$DISCORD_WEBHOOK" \
    --duration "$DURATION" \
    --attempts "$ATTEMPT" \
    --date "$DATE_STR" >> "$LOG_FILE" 2>&1
