#!/bin/bash
# 전체 크롤링 스크립트 (대용량, JSONL 저장 안 함)
# 사용법: ./crawl_full.sh [scraper_type] [max_pages] [concurrent]
# 예: ./crawl_full.sh case           # 모든 페이지, 동시 처리 3
# 예: ./crawl_full.sh judgment       # 주요판정사례 전체 크롤링
# 예: ./crawl_full.sh law 100 5      # 최대 100 페이지, 동시 처리 5

SCRAPER="${1:-case}"
MAX_PAGES="${2}"  # 페이지 수 (옵션)
CONCURRENT="${3:-3}"  # 동시 처리 수 (기본값: 3)

echo "🚀 전체 크롤링 시작"
echo "   Scraper: $SCRAPER"
if [ -z "$MAX_PAGES" ]; then
    echo "   Max Pages: 무제한 (모든 페이지)"
else
    echo "   Max Pages: $MAX_PAGES"
fi
echo "   Concurrent: $CONCURRENT"
echo "   JSONL 저장: 비활성화 (디스크 절약)"
echo ""

# Scraper 타입 검증
if [[ ! "$SCRAPER" =~ ^(law|case|adrule|interpretation|decision|mediation_case|judgment)$ ]]; then
    echo "❌ 지원하지 않는 scraper 타입: $SCRAPER"
    echo "지원 타입: law, case, adrule, interpretation, decision, mediation_case, judgment"
    exit 1
fi

# JSONL 저장 비활성화, 대용량 옵션
export SAVE_JSONL=false
# 출력 버퍼링 비활성화 (실시간 로그)
export PYTHONUNBUFFERED=1

# 로그 파일명
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="logs/crawl_full_${SCRAPER}_${TIMESTAMP}.log"

# 크롤링 실행
if [ -z "$MAX_PAGES" ]; then
    .venv/bin/python3 -u crawl.py --scraper $SCRAPER --concurrent $CONCURRENT --mode full 2>&1 | tee "$LOG_FILE"
else
    .venv/bin/python3 -u crawl.py --scraper $SCRAPER --pages "$MAX_PAGES" --concurrent $CONCURRENT --mode full 2>&1 | tee "$LOG_FILE"
fi

echo ""
echo "✅ 크롤링 완료"
echo "📊 로그: $LOG_FILE"
