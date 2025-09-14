#!/bin/bash

# =================================================================
# [통합 파이프라인] 실무용 RAG 행정해석 데이터 수집
# =================================================================

# --- 설정 ---
BASE_DIR=$(dirname "$0")/..
SCRAPER_TYPE="interpretation"

DEPT_CODE=$1
MAX_PAGES_ARG=$2
MODE=$3 # merge-only 모드를 위한 인자

# 파라미터 없는 기본 URL
START_URL="https://www.law.go.kr/cgmExpcSc.do?menuId=11&subMenuId=729&tabMenuId=773"

# 부처 코드를 포함한 경로 설정
RAW_DATA_DIR="${BASE_DIR}/data/raw/${SCRAPER_TYPE}/${DEPT_CODE}"
FINAL_DATA_DIR="${BASE_DIR}/data/final/${SCRAPER_TYPE}/${DEPT_CODE}"
LOG_DIR="${BASE_DIR}/logs"
SCRIPTS_DIR="${BASE_DIR}/scripts"
FINAL_DOCUMENT_FILE="${FINAL_DATA_DIR}/all_documents.jsonl"

export SCRAPER_RUN_ID=$(date +"%Y%m%d-%H%M%S")

# --- 사전 실행 체크 ---
if [ -z "$DEPT_CODE" ]; then
    echo "\n ERROR >>> 사용법: $0 [부처코드] (선택: [테스트 페이지 수]) (선택: merge-only)"
    echo "   예시 (전체 실행): $0 350101"
    echo "   예시 (2페이지만 실행): $0 350101 2"
    echo "   예시 (병합부터 실행): $0 350101 merge-only"
    echo "   예시 (병합부터 실행, 페이지 인자 포함): $0 350101 2 merge-only"
    exit 1
fi
echo ">>> ${SCRAPER_TYPE}의 ${DEPT_CODE} 스크래핑 작업 START"

# --- 실행 모드 결정 ---
# 인자 중 'merge-only'가 있으면 스크레이핑 단계를 건너뜀
RUN_SCRAPER=true
if [ "$MAX_PAGES_ARG" == "merge-only" ]; then
    RUN_SCRAPER=false
    MAX_PAGES_ARG="" # merge-only는 페이지 수 인자가 아니므로 비워줌
elif [ "$MODE" == "merge-only" ]; then
    RUN_SCRAPER=false
fi

if [ "$RUN_SCRAPER" = true ]; then
  # --- 0. 환경 정리 ---
  echo -e ">>> 0. 이전 작업 환경을 정리합니다..."
  # 이전 raw 파일 및 final 파일 삭제
  rm -rf "${RAW_DATA_DIR:?}"/*
  rm -f "${FINAL_DATA_DIR:?}"/*
  mkdir -p "${RAW_DATA_DIR}" "${FINAL_DATA_DIR}" "${LOG_DIR}"

  # --- 1. URL 유효성 검증 ---
  echo -e "\n>>> 1. URL 유효성을 검증합니다..."
  python "${SCRIPTS_DIR}/${SCRAPER_TYPE}/runners/run_url_checker.py" "$START_URL"
  if [ $? -ne 0 ]; then
      echo "!!! URL이 유효하지 않아 스크립트를 중단합니다."
      exit 1
  fi

  # --- 2. 통합 스크레이퍼 실행 (개별 .jsonl 파일 생성) ---
  MAX_PAGES_OPTION=""
  if [ -n "$MAX_PAGES_ARG" ]; then
      MAX_PAGES_OPTION="--max_pages $MAX_PAGES_ARG"
  fi
  echo -e "\n>>> 2. 통합 스크레이퍼를 실행하여 개별 데이터 파일을 생성합니다..."
  # shellcheck disable=SC2086
  python "${SCRIPTS_DIR}/${SCRAPER_TYPE}/runners/run_interpretation_scraper.py" \
    "$START_URL" \
    --dept_code "$DEPT_CODE" \
    $MAX_PAGES_OPTION \
    -o "${RAW_DATA_DIR}"
else
    echo -e "\n>>> 'merge-only' 모드로 실행합니다. 스크레이핑 단계를 건너뛰고 파일 통합부터 시작합니다."
    # merge-only 모드일 때는 final 디렉토리만 정리
    rm -f "${FINAL_DATA_DIR:?}"/*
    mkdir -p "${FINAL_DATA_DIR}"
fi

# --- 3. 파일 통합 ---
echo -e "\n>>> 3. 개별 파일들을 최종 파일로 병합합니다..."
# raw 폴더에 파일이 하나라도 있는지 확인
if [ -z "$(ls -A ${RAW_DATA_DIR})" ]; then
   echo "!!! raw 폴더에 스크래핑된 파일이 없습니다. 작업을 중단합니다."
   exit 1
fi

# [수정] 파일이 많을 때 'Argument list too long' 오류를 방지하기 위해 find 사용
find "${RAW_DATA_DIR}" -name "*.jsonl" -exec cat {} + > "${FINAL_DOCUMENT_FILE}"

if [ ! -s "${FINAL_DOCUMENT_FILE}" ]; then
    echo "!!! 최종 파일이 비어있습니다. 스크레이핑에 실패했을 수 있습니다."
    exit 1
fi
echo "'${FINAL_DOCUMENT_FILE}' 생성 완료"

# --- 4. 최종 결과물을 CSV로 변환 ---
echo -e "\n>>> 4. 기획자 공유를 위해 최종 결과물을 CSV로 변환합니다..."
python "${SCRIPTS_DIR}/utils/jsonl_to_csv.py" -i "${FINAL_DOCUMENT_FILE}" -o "${FINAL_DATA_DIR}/for_notion_documents.csv"

echo -e "\n 모든 작업이 완료되었습니다. 로그는 'logs/${SCRAPER_TYPE}/${SCRAPER_RUN_ID}' 폴더를 확인하세요."
