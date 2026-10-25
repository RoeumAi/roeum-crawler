#!/bin/bash
# ECS 태스크 진입점: 일일 크롤링을 시간 상한 안에서 돌린다.
#
# 상한(기본 170분)을 넘기면 timeout 이 끝내고 종료코드 124 로 실패한다.
# 00:00 KST 시작 기준 02:50 전에 끝나 03:30 임베딩 갱신과 겹치지 않는다.
# 자원 사용량(RESOURCE ...)은 run_daily.sh 가 크롤링 시도마다 로그에 남긴다(CRAWLER_RESOURCE_LOG=1, Dockerfile 기본값).
#
# BATCH_REPORT_JOB 이 있고 배치 보고 래퍼(ops/batch_report.py)가 이미지에 있으면 래퍼로 감싸
# 시작·종료를 배치 관리 시스템에 보고한다. 래퍼는 run_daily.sh 표준 출력 마지막 줄의 BATCH_RESULT 를 읽는다.
set -u

# 인자가 있으면 일회성 명령(예: CSV 넣기)을 그대로 실행한다. 일일 크롤링·배치 보고는 하지 않는다.
#   aws ecs run-task --overrides '{"containerOverrides":[{"name":"crawler","command":["python3","-m",...]}]}'
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

LIMIT="${CRAWLER_TIME_LIMIT:-170m}"
RUN=(timeout --kill-after=60s "$LIMIT" /app/run_daily.sh)

if [ -n "${BATCH_REPORT_JOB:-}" ] && [ -f /app/ops/batch_report.py ]; then
    exec python3 /app/ops/batch_report.py "$BATCH_REPORT_JOB" -- "${RUN[@]}"
fi
exec "${RUN[@]}"
