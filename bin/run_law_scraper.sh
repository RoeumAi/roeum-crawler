#!/bin/bash

# =================================================================
# [로깅 개선 최종판] 실무용 RAG 법령 데이터 수집 파이프라인
# =================================================================

# --- 설정 ---
BASE_DIR=$(dirname "$0")/..
DEPT_CODE=$1
MAX_PAGES_ARG=$2
LIST_PAGE_URL="https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd=${DEPT_CODE}"
RAW_DATA_DIR="${BASE_DIR}/data/raw/${DEPT_CODE}"
FINAL_DATA_DIR="${BASE_DIR}/data/final/${DEPT_CODE}"
LOG_DIR="${BASE_DIR}/logs"
SCRIPTS_DIR="${BASE_DIR}/scripts"
URL_LIST_FILE="${RAW_DATA_DIR}/urls.jsonl"

export SCRAPER_RUN_ID=$(date +"%Y%m%d-%H%M%S")

# --- 사전 실행 체크 ---
if [ -z "$DEPT_CODE" ]; then
    echo "\n ERROR >>> 사용법: $0 [부처코드] (선택: [테스트 페이지 수])"
    exit 1
fi

# --- 0. 환경 정리 ---
echo ">>> 0. 이전 작업 환경을 정리합니다..."
rm -f "${URL_LIST_FILE}"
rm -rf "${RAW_DATA_DIR:?}"/*
mkdir -p "${RAW_DATA_DIR}" "${FINAL_DATA_DIR}" "${LOG_DIR}"

# --- 1. URL 유효성 검증 ---
echo -e "\n>>> 1. URL 유효성을 검증합니다..."
python "${SCRIPTS_DIR}/run_url_checker.py" "$LIST_PAGE_URL"
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
python "${SCRIPTS_DIR}/run_list_scraper.py" "$LIST_PAGE_URL" $MAX_PAGES_OPTION -o "${URL_LIST_FILE}"
if [ ! -f "${URL_LIST_FILE}" ]; then
    echo "!!! '${URL_LIST_FILE}' 파일이 생성되지 않았습니다."
    exit 1
fi

# --- 3. 상세 페이지 크롤링 ---
echo -e "\n>>> 3. 상세 페이지 스크레이핑을 시작합니다..."
while IFS= read -r line; do
    URL=$(echo "$line" | jq -r '.url')
    NAME=$(echo "$line" | jq -r '.name')
    python "${SCRIPTS_DIR}/run_scraper.py" "$URL" -o "$NAME"
    sleep 1
done < "${URL_LIST_FILE}"

# --- 4. 파일 통합 ---
echo -e "\n>>> 4. 개별 파일들을 최종 파일로 병합합니다..."
cat "${RAW_DATA_DIR}"/*_document.jsonl > "${FINAL_DATA_DIR}/all_documents.jsonl"
cat "${RAW_DATA_DIR}"/*_chunks.jsonl > "${FINAL_DATA_DIR}/all_chunks.jsonl"

echo "'${FINAL_DATA_DIR}/all_documents.jsonl' 생성 완료"
echo "'${FINAL_DATA_DIR}/all_chunks.jsonl' 생성 완료"

echo -e "\n>>> 5. 기획자 공유를 위해 최종 결과물을 CSV로 변환합니다..."
python "${SCRIPTS_DIR}/utils/jsonl_to_csv.py" -i "${FINAL_DATA_DIR}/all_documents.jsonl" -o "${FINAL_DATA_DIR}/for_notion_documents.csv"
python "${SCRIPTS_DIR}/utils/jsonl_to_csv.py" -i "${FINAL_DATA_DIR}/all_chunks.jsonl" -o "${FINAL_DATA_DIR}/for_notion_chunks.csv"

echo -e "\n 모든 작업이 완료되었습니다. 로그는 'logs/law/${SCRAPER_RUN_ID}' 폴더를 확인하세요."
