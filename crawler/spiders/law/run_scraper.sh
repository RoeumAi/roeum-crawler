#!/bin/bash

# =================================================================
# 법령 목록 크롤러 실행 -> 상세 페이지 크롤러 자동 실행 스크립트
# =================================================================
# ⭐️ [핵심 수정 1] 스크립트 실행 시 부처 코드를 필수로 받도록 설정
if [ -z "$1" ]; then
    echo "❌ 사용법: $0 [부처코드] (선택: [테스트 페이지 수])"
    echo "   예시 (전체): $0 1492000"
    echo "   예시 (테스트): $0 1492000 2"
    exit 1
fi

# 1. 법령 목록 페이지 URL 설정 (예: 고용노동부 소관 법령)
DEPT_CODE=$1
LIST_PAGE_URL="https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd=${DEPT_CODE}"

# --- URL 유효성 검증 ---
echo ">>> 0. 입력된 부처 코드로 URL 유효성을 검증합니다..."
echo "   - 대상 URL: ${LIST_PAGE_URL}"
python check_url.py "$LIST_PAGE_URL"

# ⭐️ [핵심 수정 2] 검증 스크립트의 종료 코드를 확인하여 실패 시 중단
if [ $? -ne 0 ]; then
    echo "!!! URL이 유효하지 않아 스크립트를 중단합니다."
    exit 1
fi
echo "--------------------------------------------------------"


# ⭐️ [핵심 수정] 스크립트 실행 시 첫 번째 인자를 최대 페이지 수로 사용
# 인자가 없으면 "--max_pages" 옵션 없이 실행 (전체 크롤링)
# 인자가 있으면 "--max_pages [숫자]" 옵션으로 실행 (부분 크롤링)
MAX_PAGES_OPTION=""
if [ -n "$1" ]; then
    MAX_PAGES_OPTION="--max_pages $2"
    echo "🚀 테스트 모드로 실행합니다. 최대 ${2} 페이지만 크롤링합니다."
else
    echo "🚀 전체 수집 모드로 실행합니다. 감지된 모든 페이지를 크롤링합니다."
fi

# 기존 URL 목록 파일 삭제
if [ -f urls.jsonl ]; then
    rm urls.jsonl
fi

# 목록 스크레이퍼 실행 (urls.jsonl 생성)
echo ">>> 1. 법령 목록 페이지에서 URL 추출을 시작합니다..."
# shellcheck disable=SC2086
python list_scraper.py "$LIST_PAGE_URL" $MAX_PAGES_OPTION

# URL 목록 파일이 생성되었는지 확인
if [ ! -f urls.jsonl ]; then
    echo "!!! 'urls.jsonl' 파일이 생성되지 않았습니다. 목록 크롤링에 실패했습니다."
    exit 1
fi

echo -e "\n>>> 2. 추출된 URL 목록을 바탕으로 상세 페이지 스크레이핑을 시작합니다..."

# urls.jsonl 파일을 한 줄씩 읽어 상세 스크레이퍼 실행
while IFS= read -r line; do
    URL=$(echo "$line" | jq -r '.url')
    NAME=$(echo "$line" | jq -r '.name')

    if [ -z "$URL" ] || [ -z "$NAME" ]; then
        echo "   - 경고: 잘못된 라인을 건너뜁니다: $line"
        continue
    fi

    echo "--------------------------------------------------------"
    echo "   - 대상: $NAME"
    echo "   - URL: $URL"
    echo "--------------------------------------------------------"

    python law_scraper.py "$URL" -o "$NAME"

    sleep 1

done < urls.jsonl

# ⭐️ [핵심 추가] 7. 모든 스크레이핑 완료 후, 생성된 파일들을 하나로 통합
echo -e "\n>>> 3. 생성된 개별 파일들을 통합 파일로 병합합니다..."

# 기존 통합 파일이 있다면 삭제
rm -f export/all_laws_documents.jsonl
rm -f export/all_laws_chunks.jsonl

# 모든 _document.jsonl 파일을 all_documents.jsonl로 병합
cat export/*_document.jsonl > export/all_laws_documents.jsonl

# 모든 _chunks.jsonl 파일을 all_chunks.jsonl로 병합
cat export/*_chunks.jsonl > export/all_laws_chunks.jsonl

echo "✅ 'export/all_laws_documents.jsonl' 파일 생성 완료"
echo "✅ 'export/all_laws_chunks.jsonl' 파일 생성 완료"

echo -e "\n🎉 모든 작업이 완료되었습니다."