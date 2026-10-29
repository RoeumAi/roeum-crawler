#!/bin/bash
# 일일 증분 크롤링 → law/adrule 현재 시행 버전 재계산 → 결과 보고.
#
# 표준 출력 마지막 줄에 `BATCH_RESULT {...}` 를 찍고 실제 결과를 종료코드로 돌려준다
# (scripts/report/batch_result.py). 배치 관리 래퍼(batch-report)가 이 둘로 성공·부분 실패·실패를 판정한다.
#   0 = 끝까지 돎(failed>0 이면 부분 실패) / 2 = 재시도 후에도 목록 전체 실패 / 1 = 비정상 종료·재계산 실패
#
# 환경변수
#   CRAWLER_DIR        저장소 위치 (기본: 이 스크립트가 있는 곳)
#   PYTHON             파이썬 (기본: 맥미니 conda env, 없으면 python3)
#   CRAWLER_LOG_STDOUT 1이면 실행 로그를 표준 출력에도 낸다 (컨테이너 → CloudWatch Logs)
#   CRAWLER_DISCORD_SUMMARY 0이면 Discord 일일 요약을 보내지 않는다 (테스트 등)
#   CRAWLER_RESOURCE_LOG 1이면 크롤링 시도마다 최대 메모리·CPU 시간을 RESOURCE 한 줄로 로그에 남긴다 (GNU time 필요)
CRAWLER_DIR="${CRAWLER_DIR:-$(cd "$(dirname "$0")" && pwd)}"
if [ -z "$PYTHON" ]; then
    if [ -x "/Users/loum/miniconda3/envs/crawler/bin/python" ]; then
        PYTHON="/Users/loum/miniconda3/envs/crawler/bin/python"
    else
        PYTHON="python3"
    fi
fi
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DATE_STR=$(date "+%Y-%m-%d %H:%M")
mkdir -p "$CRAWLER_DIR/logs"
LOG_FILE="$CRAWLER_DIR/logs/daily_${TIMESTAMP}.log"

# .env 가 있는 환경(맥미니)에서만 웹훅을 읽는다. 컨테이너는 환경변수로 받는다.
# 웹훅은 argv(ps 노출)가 아닌 환경변수로 전달. 채널별 값이 없으면 기존 DISCORD_WEBHOOK 이 기본값.
read_env() {
    [ -f "$CRAWLER_DIR/.env" ] && grep "^$1=" "$CRAWLER_DIR/.env" | cut -d'=' -f2-
}
export DISCORD_WEBHOOK="${DISCORD_WEBHOOK:-$(read_env DISCORD_WEBHOOK)}"
export DISCORD_WEBHOOK_DATA_COLLECT="${DISCORD_WEBHOOK_DATA_COLLECT:-$(read_env DISCORD_WEBHOOK_DATA_COLLECT)}"
export DISCORD_WEBHOOK_BATCH="${DISCORD_WEBHOOK_BATCH:-$(read_env DISCORD_WEBHOOK_BATCH)}"
export ROEUM_ENV="${ROEUM_ENV:-prod}"

# 실행 로그를 파일에 남기고, CRAWLER_LOG_STDOUT=1 이면 표준 출력에도 낸다.
log() {
    if [ "${CRAWLER_LOG_STDOUT:-0}" = "1" ]; then
        tee -a "$LOG_FILE"
    else
        cat >> "$LOG_FILE"
    fi
}

# ── 이전 프로세스 중복 실행 방지 ──
LOCKFILE="${CRAWLER_LOCKFILE:-/tmp/roeum_crawler.lock}"
if [ -f "$LOCKFILE" ]; then
    PID=$(cat "$LOCKFILE")
    if kill -0 "$PID" 2>/dev/null; then
        # 공통 알림 형식(scripts/notify/alert_format.py) — 스킵은 배치 채널(미설정 시 기존 웹훅)
        "$PYTHON" -u "$CRAWLER_DIR/scripts/notify/alert_format.py" --channel batch \
            --severity WARN --kind 배치 --target "roeum-crawler(daily)" \
            --summary "이전 실행(PID=${PID})이 아직 진행 중이라 ${DATE_STR} 실행을 건너뜁니다." \
            --trace "스킵 ${DATE_STR} · lock ${LOCKFILE} · PID ${PID}" \
            --search "${CRAWLER_LOG_LOCATION:-맥미니 ${CRAWLER_DIR}/logs/ 의 직전 daily_*.log}" \
            --detail "조치: 이전 실행이 멈췄다면 PID 확인 후 종료" >/dev/null 2>&1 || true
        # 이번 실행은 아무것도 하지 않았다 → 실패로 보고해 겹친 실행이 드러나게 한다.
        echo "BATCH_RESULT {\"processed\":0,\"failed\":1,\"failedTargets\":[\"crawl:locked\"],\"summary\":\"이전 실행(PID=${PID}) 진행 중이라 건너뜀\"}"
        exit 1
    fi
fi
echo $$ > "$LOCKFILE"
RESULT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/roeum_crawler_result.XXXXXX")
trap 'rm -f "$LOCKFILE"; rm -rf "$RESULT_DIR"' EXIT

cd "$CRAWLER_DIR"

START_TIME=$(date +%s)

# ── 외부 수집원(law.go.kr 등) 일시 장애 대비 재시도 설정 ──
# crawl.py 는 URL 목록조차 못 모은 스크래퍼가 있으면 종료코드 2를 반환한다.
# 2회차부터는 그 스크래퍼만 다시 돈다(판례 목록 20분을 다시 돌지 않게).
MAX_ATTEMPTS=3            # 총 시도 횟수 (최초 1 + 재시도 2)
RETRY_WAIT_SECONDS="${RETRY_WAIT_SECONDS:-900}"   # 재시도 전 대기 (15분) — law.go.kr 새벽 점검 창을 넘기기 위함

{
    echo "======================================"
    echo "일일 업데이트 시작: $(date)"
    echo "======================================"
} | log

# 자원 사용량 줄은 로그 안(BATCH_RESULT 앞)에 남겨야 래퍼가 마지막 줄로 결과를 읽을 수 있다.
TIME_CMD=()
# macOS(BSD) time 은 -f 를 모르므로 GNU time 일 때만 쓴다.
if [ "${CRAWLER_RESOURCE_LOG:-0}" = "1" ] && /usr/bin/time --version >/dev/null 2>&1; then
    TIME_CMD=(/usr/bin/time -f "RESOURCE max_rss_kb=%M user_s=%U sys_s=%S elapsed_s=%e")
fi

ATTEMPT=1
SCRAPER_ARGS=()
RESULT_ARGS=()
while true; do
    RESULT_JSON="$RESULT_DIR/attempt_${ATTEMPT}.json"
    RESULT_ARGS+=(--attempt-json "$RESULT_JSON")
    {
        echo ""
        echo "▶ 크롤링 시도 ${ATTEMPT}/${MAX_ATTEMPTS}: $(date) ${SCRAPER_ARGS[*]}"
    } | log
    "${TIME_CMD[@]}" "$PYTHON" -u crawl.py --mode update --concurrent 3 --result-json "$RESULT_JSON" "${SCRAPER_ARGS[@]}" 2>&1 | log
    EXIT_CODE=${PIPESTATUS[0]}

    # 종료코드 2(=URL 목록 수집 실패)일 때만 재시도. 그 외(정상/치명)면 종료.
    if [ "$EXIT_CODE" -ne 2 ] || [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
        break
    fi

    RETRY=$("$PYTHON" scripts/report/batch_result.py retry-scrapers "$RESULT_JSON")
    if [ -z "$RETRY" ]; then
        break
    fi
    # shellcheck disable=SC2206 # 스크래퍼 이름은 공백 없는 키
    SCRAPER_ARGS=(--scraper $RETRY)
    echo "⚠️ 외부 수집원 오류로 URL 목록 수집 실패(${RETRY}) — ${RETRY_WAIT_SECONDS}초 후 재시도" | log
    ATTEMPT=$((ATTEMPT + 1))
    sleep "$RETRY_WAIT_SECONDS"
done

{
    echo "======================================"
    echo "law/adrule 현재 시행 버전 재계산 시작: $(date)"
    echo "======================================"
} | log
"$PYTHON" -u scripts/core/flows/refresh_current_status_flow.py 2>&1 | log
REFRESH_EXIT_CODE=${PIPESTATUS[0]}

END_TIME=$(date +%s)
DURATION=$(( (END_TIME - START_TIME) / 60 ))

echo "종료: $(date)" | log

# 알림 메시지 생성·전송은 테스트 가능한 파이썬 모듈로 위임 (scripts/notify/daily_summary.py)
# 웹훅이 없으면 보내지 않는다(CRAWLER_DISCORD_SUMMARY=0 으로 끌 수도 있다).
if [ "${CRAWLER_DISCORD_SUMMARY:-1}" != "0" ]; then
    "$PYTHON" -u -m scripts.notify.daily_summary \
        "${RESULT_ARGS[@]}" \
        --duration "$DURATION" \
        --attempts "$ATTEMPT" \
        --date "$DATE_STR" \
        --target "roeum-crawler(daily)" \
        --exit-code "$EXIT_CODE" \
        --refresh-exit-code "$REFRESH_EXIT_CODE" 2>&1 | log
fi

# b2c 공개 법률 문서 페이지 갱신 요청. 실패해도 크롤링 결과에 영향 없음(stdout 은 로그로만, BATCH_RESULT 가 마지막 줄).
"$PYTHON" -u -m scripts.notify.repo_dispatch 2>&1 | log || true

# 마지막 줄: BATCH_RESULT (파일과 표준 출력 양쪽). 종료코드는 실제 결과.
BATCH_LINE=$("$PYTHON" scripts/report/batch_result.py final \
    "${RESULT_ARGS[@]}" \
    --last-crawl-exit-code "$EXIT_CODE" \
    --refresh-exit-code "$REFRESH_EXIT_CODE" \
    --attempts "$ATTEMPT" \
    --duration-minutes "$DURATION")
FINAL_EXIT_CODE=$?
if [ -z "$BATCH_LINE" ]; then
    BATCH_LINE='BATCH_RESULT {"processed":0,"failed":1,"failedTargets":["report:batch-result"],"summary":"결과 집계 실패"}'
    FINAL_EXIT_CODE=1
fi
echo "$BATCH_LINE" >> "$LOG_FILE"
echo "$BATCH_LINE"
exit "$FINAL_EXIT_CODE"
