#!/bin/bash
CRAWLER_DIR="/Users/loum/loum/roeum-crawler"
PYTHON="/Users/loum/miniconda3/envs/crawler/bin/python"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DATE_STR=$(date "+%Y-%m-%d %H:%M")
LOG_FILE="$CRAWLER_DIR/logs/weekly_${TIMESTAMP}.log"
DISCORD_WEBHOOK=$(grep '^DISCORD_WEBHOOK=' "$CRAWLER_DIR/.env" | cut -d'=' -f2-)

mkdir -p "$CRAWLER_DIR/logs"
cd "$CRAWLER_DIR"

START_TIME=$(date +%s)

# ── 외부 수집원(law.go.kr 등) 일시 장애 대비 전체 재시도 설정 (run_daily.sh 와 동일) ──
MAX_ATTEMPTS=3            # 총 시도 횟수 (최초 1 + 재시도 2)
RETRY_WAIT_SECONDS=900   # 재시도 전 대기 (15분)

echo "======================================" >> "$LOG_FILE"
echo "주간 업데이트 시작: $(date)" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"

ATTEMPT=1
while true; do
    echo "" >> "$LOG_FILE"
    echo "▶ 크롤링 시도 ${ATTEMPT}/${MAX_ATTEMPTS}: $(date)" >> "$LOG_FILE"
    "$PYTHON" -u crawl.py --mode update --since 7 --concurrent 3 >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?

    # 종료코드 2(=URL 목록 수집 실패)일 때만 재시도. 그 외(정상/치명)면 종료.
    if [ "$EXIT_CODE" -ne 2 ] || [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
        break
    fi

    echo "⚠️ 외부 수집원 오류로 URL 목록 수집 실패 — ${RETRY_WAIT_SECONDS}초 후 재시도" >> "$LOG_FILE"
    ATTEMPT=$((ATTEMPT + 1))
    sleep "$RETRY_WAIT_SECONDS"
done

END_TIME=$(date +%s)
DURATION=$(( (END_TIME - START_TIME) / 60 ))

echo "종료: $(date)" >> "$LOG_FILE"

# 알림 메시지 생성·전송은 테스트 가능한 파이썬 모듈로 위임 (run_daily.sh 와 동일 모듈)
"$PYTHON" -u scripts/notify/daily_summary.py \
    --log "$LOG_FILE" \
    --webhook "$DISCORD_WEBHOOK" \
    --duration "$DURATION" \
    --attempts "$ATTEMPT" \
    --date "$DATE_STR" \
    --title "주간 크롤러 업데이트" >> "$LOG_FILE" 2>&1
