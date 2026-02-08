#!/bin/bash

##############################################################################
# roeum-crawler 주기적 업데이트 스크립트
#
# 설명:
#   - 기존 문서는 변경사항만 감지하여 업데이트
#   - JSONL 파일 생성 안 함 (MongoDB에만 저장)
#   - 빠른 업데이트용 (1시간~1일마다 권장)
#
# 사용법:
#   ./crawl_update.sh law              # 모든 법령 업데이트
#   ./crawl_update.sh case             # 모든 판례 업데이트
#   ./crawl_update.sh law 50           # 첫 50페이지만 업데이트
#   ./crawl_update.sh case 100         # 첫 100페이지만 업데이트
#
# 예약 작업 (crontab):
#   # 매일 오전 2시에 법령 업데이트
#   0 2 * * * cd /Users/woong/Documents/Roeum/roeum-crawler && ./crawl_update.sh law >> logs/update_law.log 2>&1
#
#   # 매일 오전 4시에 판례 업데이트
#   0 4 * * * cd /Users/woong/Documents/Roeum/roeum-crawler && ./crawl_update.sh case >> logs/update_case.log 2>&1
#
##############################################################################

set -e  # 에러 발생 시 즉시 종료

# 프로젝트 루트 경로
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

# 파라미터 확인
if [ $# -lt 1 ]; then
    echo "❌ 사용법: $0 <scraper_type> [max_pages]"
    echo ""
    echo "예시:"
    echo "  $0 law              # 모든 법령 업데이트"
    echo "  $0 case             # 모든 판례 업데이트"
    echo "  $0 law 50           # 첫 50페이지만 업데이트"
    echo "  $0 case 100         # 첫 100페이지만 업데이트"
    exit 1
fi

SCRAPER_TYPE="$1"
MAX_PAGES="${2:-}"  # 두 번째 파라미터는 선택사항

# Scraper 타입 검증
if [[ ! "$SCRAPER_TYPE" =~ ^(law|case|adrule|interpretation)$ ]]; then
    echo "❌ 지원하지 않는 scraper 타입: $SCRAPER_TYPE"
    echo "지원 타입: law, case, adrule, interpretation"
    exit 1
fi

# 로그 디렉토리 생성
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

# 로그 파일명 (타임스탐프 포함)
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/update_${SCRAPER_TYPE}_${TIMESTAMP}.log"

echo "🔄 업데이트 시작: $SCRAPER_TYPE"
echo "📝 로그 파일: $LOG_FILE"
echo ""

# 환경 변수 설정
export SAVE_JSONL=false  # JSONL 파일 생성 비활성화 (빠른 업데이트)
export PYTHONUNBUFFERED=1  # Python 출력 버퍼링 비활성화 (실시간 로그)

# Prefect Flow 실행
if [ -z "$MAX_PAGES" ]; then
    # 모든 페이지 업데이트
    echo "⏱️  전체 페이지 크롤링 시작..."
    python -m prefect.cli deployment run "$SCRAPER_TYPE/scraper" \
        --var scraper_type="$SCRAPER_TYPE" \
        2>&1 | tee -a "$LOG_FILE"
else
    # 제한된 페이지만 업데이트
    echo "⏱️  첫 $MAX_PAGES 페이지 크롤링 시작..."
    python -m prefect.cli deployment run "$SCRAPER_TYPE/scraper" \
        --var scraper_type="$SCRAPER_TYPE" \
        --var max_pages="$MAX_PAGES" \
        2>&1 | tee -a "$LOG_FILE"
fi

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ $SCRAPER_TYPE 업데이트 완료!"
    echo "📊 결과 확인: $LOG_FILE"
else
    echo ""
    echo "❌ $SCRAPER_TYPE 업데이트 실패 (종료 코드: $EXIT_CODE)"
    echo "🔍 로그 확인: $LOG_FILE"
fi

exit $EXIT_CODE
