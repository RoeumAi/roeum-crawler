#!/bin/bash
# Internal body: invoke run_daily.sh/run_weekly.sh to acquire the shared lock.
set -u
cd "$CRAWLER_DIR" || exit 1
DAYS="${1:?days required}"
PYTHON="$CRAWLER_PYTHON"
CONCURRENT="${CRAWLER_CONCURRENT:-3}"
case "$CONCURRENT" in ''|*[!0-9]*|0) echo "Invalid CRAWLER_CONCURRENT"; exit 1;; esac
MAX_ATTEMPTS=3
RETRY_WAIT_SECONDS=900
ATTEMPT=1
START_TIME=$(date +%s)
LOG_FILE="${CRAWLER_LOG_FILE:-/dev/null}"

while true; do
    echo "크롤링 시도 ${ATTEMPT}/${MAX_ATTEMPTS}: $(date)"
    "$PYTHON" -u crawl.py --mode update --since "$DAYS" --concurrent "$CONCURRENT"
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -ne 2 ] || [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then break; fi
    ATTEMPT=$((ATTEMPT + 1))
    echo "URL 수집 실패: ${RETRY_WAIT_SECONDS}초 후 재시도"
    sleep "$RETRY_WAIT_SECONDS"
done

if [ "$DAYS" = 1 ]; then
    "$PYTHON" -u scripts/core/flows/refresh_current_status_flow.py
    REFRESH_CODE=$?
    echo "refresh_exit_code=$REFRESH_CODE"
    if [ "$EXIT_CODE" -eq 0 ] && [ "$REFRESH_CODE" -ne 0 ]; then EXIT_CODE=$REFRESH_CODE; fi
fi

# Preserve the former GitHub-only weekly nodong.kr collection on Mac mini.
if [ "$DAYS" = 7 ] || [ "$(date +%u)" = 1 ]; then
    "$PYTHON" -u scripts/nodong/crawl_nodong.py --concurrent "$CONCURRENT"
    NODONG_CODE=$?
    echo "nodong_exit_code=$NODONG_CODE"
    if [ "$EXIT_CODE" -eq 0 ] && [ "$NODONG_CODE" -ne 0 ]; then EXIT_CODE=$NODONG_CODE; fi
fi

END_TIME=$(date +%s)
DURATION=$(( (END_TIME - START_TIME) / 60 ))
echo "종료: $(date) exit=$EXIT_CODE"
DISCORD_WEBHOOK=$(sed -n 's/^DISCORD_WEBHOOK=//p' .env 2>/dev/null)
if [ -n "$DISCORD_WEBHOOK" ]; then
    "$PYTHON" -u scripts/notify/daily_summary.py \
        --log "$LOG_FILE" --webhook "$DISCORD_WEBHOOK" \
        --duration "$DURATION" --attempts "$ATTEMPT" --date "$(date '+%Y-%m-%d %H:%M')"
    NOTIFY_CODE=$?
    if [ "$EXIT_CODE" -eq 0 ] && [ "$NOTIFY_CODE" -ne 0 ]; then EXIT_CODE=$NOTIFY_CODE; fi
fi
exit "$EXIT_CODE"
