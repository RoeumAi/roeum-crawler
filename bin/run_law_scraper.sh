#!/bin/bash

# =================================================================
# [로깅 개선 최종판] 실무용 RAG 법령 데이터 수집 파이프라인
# =================================================================

# --- 설정 ---
BASE_DIR=$(dirname "$0")/..
SCRAPER_TYPE="law"

DEPT_CODE=$1
MAX_PAGES_ARG=$2
CONCURRENCY=${3:-5}

LIST_PAGE_URL="https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd=${DEPT_CODE}"
RAW_DATA_DIR="${BASE_DIR}/data/raw/${SCRAPER_TYPE}/${DEPT_CODE}"
FINAL_DATA_DIR="${BASE_DIR}/data/final/${SCRAPER_TYPE}/${DEPT_CODE}"
LOG_DIR="${BASE_DIR}/logs"
SCRIPTS_DIR="${BASE_DIR}/scripts"
URL_LIST_FILE="${RAW_DATA_DIR}/urls.jsonl"

export SCRAPER_RUN_ID=$(date +"%Y%m%d-%H%M%S")

# --- 사전 실행 체크 ---
if [ -z "$DEPT_CODE" ]; then
    echo "\n ERROR >>> 사용법: $0 [부처코드] (선택: [테스트 페이지 수])"
    echo "   예시 (전체, 10개 병렬): $0 1492000 '' 10"
    echo "   예시 (2페이지만, 5개 병렬): $0 1492000 2 5"
    exit 1
fi

echo ">>> ${SCRAPER_TYPE}의 ${DEPT_CODE} 스크래핑 작업 START"

# --- 0. 환경 정리 ---
echo -e ">>> 0. 이전 작업 환경을 정리합니다..."
rm -f "${URL_LIST_FILE}"
rm -rf "${RAW_DATA_DIR:?}"/*
mkdir -p "${RAW_DATA_DIR}" "${FINAL_DATA_DIR}" "${LOG_DIR}"

# --- 1. URL 유효성 검증 ---
echo -e "\n>>> 1. URL 유효성을 검증합니다..."
python "${SCRIPTS_DIR}/${SCRAPER_TYPE}/runners/run_url_checker.py" "$LIST_PAGE_URL"
if [ $? -ne 0 ]; then
    echo "!!! URL이 유효하지 않아 스크립트를 중단합니다."
    exit 1
fi

# --- 2. 목록 크롤링 ---
MAX_PAGES_OPTION=""
if [ -n "$MAX_PAGES_ARG" ]; then
    MAX_PAGES_OPTION="--max_pages $MAX_PAGES_ARG"
fi
echo -e "\n>>> 2. 법령 목록 페이지에서 URL 추출을 시작합니다..."
# shellcheck disable=SC2086
python "${SCRIPTS_DIR}/${SCRAPER_TYPE}/runners/run_list_scraper.py" "$LIST_PAGE_URL" $MAX_PAGES_OPTION -o "${URL_LIST_FILE}"
if [ ! -f "${URL_LIST_FILE}" ]; then
    echo "!!! '${URL_LIST_FILE}' 파일이 생성되지 않았습니다."
    exit 1
fi

# --- 3. 상세 페이지 크롤링 ---

# -L 1: 한 번에 한 줄씩 처리
# -P ${CONCURRENCY}: 동시에 실행할 프로세스 개수
# -I {}: 읽어온 한 줄을 대체할 플레이스홀더

echo -e "\n>>> 3. 상세 페이지 스크레이핑을 시작합니다..."

#run_single_scraper() {
#    line="$1"
#    URL=$(echo "$line" | jq -r ".url")
#    NAME=$(echo "$line" | jq -r ".name")
#
#    echo "   - [병렬 실행 시작] $NAME"
#    python "${SCRIPTS_DIR}/run_scraper.py" "$URL" -d "${DEPT_CODE}" -o "$NAME"
#    echo "   - [병렬 실행 완료] $NAME"
#}
#
#export -f run_single_scraper
#export SCRIPTS_DIR
#export DEPT_CODE
#
#cat "${URL_LIST_FILE}" | xargs -L 1 -P ${CONCURRENCY} -I {} bash -c 'run_single_scraper "{}"'

while IFS= read -r line; do
    URL=$(echo "$line" | jq -r '.url')
    NAME=$(echo "$line" | jq -r '.name')
    python "${SCRIPTS_DIR}/${SCRAPER_TYPE}/runners/run_scraper.py" "$URL" -d "${DEPT_CODE}" -o "$NAME"
    sleep 1
done < "${URL_LIST_FILE}"

# --- 4. 파일 통합 ---
echo -e "\n>>> 4. 개별 파일들을 최종 파일로 병합합니다..."
cat "${RAW_DATA_DIR}"/*_document.jsonl > "${FINAL_DATA_DIR}/all_${SCRAPER_TYPE}_documents.jsonl"
cat "${RAW_DATA_DIR}"/*_chunks.jsonl > "${FINAL_DATA_DIR}/all_${SCRAPER_TYPE}_chunks.jsonl"

echo "'${FINAL_DATA_DIR}/all_${SCRAPER_TYPE}_documents.jsonl' 생성 완료"
echo "'${FINAL_DATA_DIR}/all_${SCRAPER_TYPE}_chunks.jsonl' 생성 완료"

# --- 5. 최종 결과물을 CSV로 변환 ---
echo -e "\n>>> 5. 기획자 공유를 위해 최종 결과물을 CSV로 변환합니다..."
python "${SCRIPTS_DIR}/utils/jsonl_to_csv.py" -i "${FINAL_DATA_DIR}/all_${SCRAPER_TYPE}_documents.jsonl" -o "${FINAL_DATA_DIR}/for_notion_${SCRAPER_TYPE}_documents.csv"
python "${SCRIPTS_DIR}/utils/jsonl_to_csv.py" -i "${FINAL_DATA_DIR}/all_${SCRAPER_TYPE}_chunks.jsonl" -o "${FINAL_DATA_DIR}/for_notion_${SCRAPER_TYPE}_chunks.csv"

echo -e "\n 모든 작업이 완료되었습니다. 로그는 'logs/${SCRAPER_TYPE}/${SCRAPER_RUN_ID}' 폴더를 확인하세요."
