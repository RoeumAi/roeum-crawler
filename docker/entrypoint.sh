#!/bin/bash
# ECS 태스크 진입점: 일일 크롤링을 시간 상한 안에서 돌린다.
#
# 상한(기본 170분)을 넘기면 timeout 이 끝내고 종료코드 124 로 실패한다.
# 00:00 KST 시작 기준 02:50 전에 끝나 03:30 임베딩 갱신과 겹치지 않는다.
# 마지막에 최대 메모리·CPU 시간을 한 줄(RESOURCE ...)로 표준 오류에 남겨 태스크 크기 조정 근거로 쓴다.
#
# BATCH_REPORT_JOB 이 있고 배치 보고 래퍼(ops/batch_report.py)가 이미지에 있으면 래퍼로 감싸
# 시작·종료를 배치 관리 시스템에 보고한다. 래퍼는 run_daily.sh 표준 출력 마지막 줄의 BATCH_RESULT 를 읽는다.
set -u
LIMIT="${CRAWLER_TIME_LIMIT:-170m}"
RUN=(timeout --kill-after=60s "$LIMIT"
     /usr/bin/time -f "RESOURCE max_rss_kb=%M user_s=%U sys_s=%S elapsed_s=%e"
     /app/run_daily.sh)

if [ -n "${BATCH_REPORT_JOB:-}" ] && [ -f /app/ops/batch_report.py ]; then
    exec python3 /app/ops/batch_report.py "$BATCH_REPORT_JOB" -- "${RUN[@]}"
fi
exec "${RUN[@]}"
