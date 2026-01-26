# 즉시 실행 가능한 개선 plan

## 🚨 발견된 문제

### 함수명 불일치
```
law      → fetch_law_urls()      ✅
adrule   → fetch_urls()          ❌ (fetch_adrule_urls 기대)
case     → fetch_urls()          ❌ (fetch_case_urls 기대)
```

**현재 상황:** unified_scraper_flow.py에서 `fetch_adrule_urls`, `fetch_case_urls`를 찾으려고 하므로 **AttributeError 발생 가능**

---

## 💡 해결 방법 3가지

### ✅ 방법 1: 함수명 통일 (가장 간단, 권장)

```python
# 모든 scraper에서 같은 함수명 사용
async def fetch_urls(start_url, max_pages_arg):
    """통일된 함수명"""
    pass
```

**수정 파일:**
1. `scripts/law/logic/list_scraper.py`: `fetch_law_urls` → `fetch_urls`로 이름 변경
2. `scripts/core/flows/unified_scraper_flow.py`: 동적 함수명 생성 제거

**작업량:** 20분

---

### ✅ 방법 2: Flow에서 동적 매핑

```python
# unified_scraper_flow.py에서 함수명 매핑
FETCH_FUNC_NAMES = {
    'law': 'fetch_law_urls',
    'adrule': 'fetch_urls',
    'case': 'fetch_urls',
}

fetch_func_name = FETCH_FUNC_NAMES.get(scraper_type)
```

**수정 파일:**
1. `scripts/core/flows/unified_scraper_flow.py`만 수정

**작업량:** 10분

---

### ✅ 방법 3: Scraper 클래스 기반 (가장 깔끔하지만 시간 소요)

```python
class BaseScraper:
    async def fetch_urls(self, start_url, max_pages):
        pass

class LawScraper(BaseScraper):
    async def fetch_urls(self, start_url, max_pages):
        # law 구현
        pass
```

**작업량:** 2-3시간

---

## 📋 추천 우선순위

**즉시 수행:** 방법 2 (Flow 수정만, 10분)  
**단기:** 방법 1 (함수명 통일, 20분)  
**장기:** 방법 3 (클래스 기반 리팩토링)

---

## 다른 개선 사항들

### 1. 반환 값 스키마 검증
- adrule은 이미지 처리 실패 가능성 높음
- case는 청크 배열 반환 (다른 구조)
- Flow에서 검증 필요

### 2. MongoDB 저장 에러 처리
- 각 scraper의 scrape_and_save()에서 직접 저장
- Flow는 저장 여부를 알 수 없음
- 반환값으로 상태 전달 필요

### 3. 로깅 통합
- 각 scraper의 logger 분산
- Prefect Task 로그와 섞임
- 통합 로거 필요

