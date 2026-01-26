#!/bin/bash

# 🚀 Roeum Crawler - 통합 크롤링 실행 스크립트
# Prefect 워커 풀 설정 + 크롤링 자동 실행

set -e  # 에러 발생 시 스크립트 중단

# 기본 변수
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
PREFECT_SERVER_LOG="$LOG_DIR/prefect_server.log"
PREFECT_WORKER_LOG="$LOG_DIR/prefect_worker.log"
SCRAPER_TYPE="${1:-law}"
MAX_PAGES="${2:-1}"

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

# 색상 정의
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=====================================================================${NC}"
echo "🚀 Roeum Crawler - Prefect 워커 풀 설정 및 크롤링 시작"
echo -e "${GREEN}=====================================================================${NC}"

# 1. Prefect 서버 상태 확인 및 시작
echo -e "\n${YELLOW}📍 Step 1: Prefect 서버 확인 및 시작${NC}"
if lsof -Pi :4200 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo "✅ Prefect 서버가 이미 실행 중입니다 (포트 4200)"
else
    echo "🔄 Prefect 서버를 시작합니다..."
    prefect server start > "$PREFECT_SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    echo "✅ Prefect 서버 시작됨 (PID: $SERVER_PID)"
    sleep 3
fi

# 2. Prefect 워커 확인 및 시작
echo -e "\n${YELLOW}📍 Step 2: Prefect 워커 확인 및 시작${NC}"
WORKER_COUNT=$(ps aux | grep -c "prefect worker start" | grep -v grep || true)
if [ "$WORKER_COUNT" -gt 0 ]; then
    echo "✅ Prefect 워커가 이미 실행 중입니다"
else
    echo "🔄 Prefect 워커를 시작합니다..."
    prefect worker start --pool default > "$PREFECT_WORKER_LOG" 2>&1 &
    WORKER_PID=$!
    echo "✅ Prefect 워커 시작됨 (PID: $WORKER_PID)"
    sleep 3
fi

# 3. Work pool 상태 확인
echo -e "\n${YELLOW}📍 Step 3: Work Pool 상태 확인${NC}"
prefect work-pool ls | grep "default" && echo "✅ Work pool 'default' 준비 완료"

# 4. 크롤링 시작
echo -e "\n${GREEN}=====================================================================${NC}"
echo "🚀 크롤링 시작"
echo -e "${GREEN}=====================================================================${NC}"
echo "📊 설정:"
echo "   - Scraper: $SCRAPER_TYPE"
echo "   - Max Pages: $MAX_PAGES"
echo ""

cd "$SCRIPT_DIR"
python3 crawl.py --scraper "$SCRAPER_TYPE" --pages "$MAX_PAGES"

CRAWL_STATUS=$?

# 5. 결과 정리
echo -e "\n${GREEN}=====================================================================${NC}"
if [ $CRAWL_STATUS -eq 0 ]; then
    echo "✅ 크롤링 완료!"
else
    echo -e "${RED}❌ 크롤링 중 에러 발생${NC}"
fi
echo -e "${GREEN}=====================================================================${NC}"

# 로그 위치 안내
echo ""
echo "📋 로그 파일:"
echo "   - Prefect 서버: $PREFECT_SERVER_LOG"
echo "   - Prefect 워커: $PREFECT_WORKER_LOG"
echo ""
echo "🔍 로그 확인 명령어:"
echo "   tail -f $PREFECT_SERVER_LOG"
echo "   tail -f $PREFECT_WORKER_LOG"
echo ""

exit $CRAWL_STATUS
