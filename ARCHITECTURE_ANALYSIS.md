# 통합 Scraper Flow 아키텍처 상세 분석

## 📋 개요

현재 구조는 **완전 다른 3개의 스크래퍼(law, adrule, case)를 한 곳에서 관리**하는 설계입니다.  
각각 목록 수집 방식, HTML 파싱 방식, 데이터 추출 로직이 완전히 다르지만, **Prefect Flow로 통합**하고 있습니다.

---

## 🔍 Step-by-Step 분석

### STEP 1: URL 수집 방식 비교

#### 1-1. `law` (법령)
```
목록 페이지: https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd=1492000
URL 추출 방식:
  - onclick 속성: lsReturnSearch(lsiSeq, efYd, ...)
  - URL 구성: https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={lsiSeq}&efYd={efYd}
  - 테이블 선택자: #resultTableDiv
```

**특징:**
- ✅ 정규 정규표현식으로 안정적으로 파싱 가능
- ✅ 페이지네이션: `pageSearch('lsListDiv', 'page_num')` 함수 사용
- ✅ 함수명: `fetch_law_urls()`

---

#### 1-2. `adrule` (행정규칙)
```
목록 페이지: https://www.law.go.kr/LSW/admRulSc.do?cptOfiCd=1492000
URL 추출 방식:
  - onclick 속성: admRulReturnSearch(lsiSeq, ...)
  - URL 구성: https://www.law.go.kr/admRulInfoP.do?admRulSeq={lsiSeq}
  - 테이블 선택자: #resultAdmRulTableDiv
```

**특징:**
- ⚠️ **법령과 다른 onclick 함수명** (`admRulReturnSearch`)
- ⚠️ **다른 테이블 ID** (`resultAdmRulTableDiv`)
- ⚠️ 페이지네이션: `pageSearch('admRulListDiv', 'page_num')`
- ✅ 함수명: `fetch_urls()` (⚡ **문제: 다른 함수명**)

---

#### 1-3. `case` (판례)
```
목록 페이지: https://www.law.go.kr/LSW/precSc.do?cptOfiCd=1492000
URL 추출 방식:
  - onclick 속성: openDetail('precInfoP.do?precSeq=...')
  - URL 구성: 직접 상대 경로로 제공됨
  - 테이블 선택자: #resultTableDiv
```

**특징:**
- ⚠️ **onclick 함수명 완전히 다름** (`openDetail`)
- ⚠️ **URL 추출 방식 전혀 다름** (직접 상대경로 제공)
- ⚠️ 페이지네이션: `pageSearch('lsListDiv', 'page_num')`
- ✅ 함수명: `fetch_urls()` (⚡ **문제: 다른 함수명**)

---

### STEP 2: 문서 스크래핑 방식 비교

#### 2-1. `law` (법령)
```python
def scrape_and_save(url, output_dir, output_name, dept_code, save_to_db=True):
    # 1. Playwright로 페이지 로드
    # 2. BeautifulSoup으로 HTML 파싱
    # 3. parse_law_html() → 통합 스키마로 변환
    # 4. MongoDB에 직접 저장
    
    반환값: {"doc_id": "...", "content": "...", ...}
```

**파싱 로직:**
- 조문별 분석 (제1조, 제2조...)
- 부칙 처리
- 메타데이터 추출 (시행일자, 개정내용 등)

---

#### 2-2. `adrule` (행정규칙)
```python
def scrape_and_save(url, output_dir, output_name, dept_code, save_to_db=True):
    # 1. Playwright로 페이지 로드
    # 2. BeautifulSoup + 정규표현식
    # 3. parse_law_html() → 통합 스키마 (법령과 동일)
    # 4. MongoDB에 직접 저장
    
    특징: 
    - ⚠️ 텍스트 기반 (이미지 OCR 필요 시 CLOVA OCR 사용)
    - ⚠️ 이미지 내 테이블 처리 (OpenCV + Tesseract)
```

**파싱 로직:**
- law와 유사하지만, **이미지 처리 추가**
- OCR 필요 시 CLOVA API 호출
- 더 복잡한 조건부 처리

---

#### 2-3. `case` (판례)
```python
def scrape_and_save(url, output_dir, output_name, dept_code, save_to_db=True):
    # 1. Playwright로 페이지 로드
    # 2. BeautifulSoup으로 HTML 파싱
    # 3. parse_case_law_content() → 청크 기반 분할
    # 4. MongoDB에 직접 저장
    
    특징:
    - 의미 단위 청크 분할 (판시사항, 판결요지, 참조조문 등)
    - 다양한 섹션 매핑
```

**파싱 로직:**
- **완전히 다른 구조** (청크 기반)
- 섹션 인식 및 분류
- 판례특화 메타데이터

---

### STEP 3: MongoDB 저장 방식

#### 현재 구조
```
scrape_and_save() 
  ↓
  각 scraper에서 직접 MongoDB에 저장
  ↓
  UnifiedDocumentRepository.upsert_document()
  
문제점:
❌ 저장 로직이 각 scraper에 분산됨
❌ Flow에서 "저장되었는가?" 확인 불가
❌ MongoDB 오류 발생 시 Flow에서 감지 어려움
```

---

## ⚠️ 현재 구조의 문제점

### 1️⃣ **함수명 불일치**
```python
# unified_scraper_flow.py (Line 47)
fetch_func_name = f"fetch_{scraper_type}_urls"  # ← law만 맞음
fetch_urls = getattr(scraper_module, fetch_func_name, None)

# 결과:
law      → fetch_law_urls()      ✅ 존재
adrule   → fetch_adrule_urls()   ❌ 없음! → fetch_urls() 호출됨
case     → fetch_case_urls()     ❌ 없음! → fetch_urls() 호출됨
```

**현실:**
- `adrule`과 `case`는 `fetch_urls()`를 가지고 있음
- Flow가 `fetch_adrule_urls()`, `fetch_case_urls()`를 찾으려고 하면 **AttributeError** 발생!

---

### 2️⃣ **페이지 수집 로직의 세부 차이**

| 항목 | law | adrule | case |
|------|-----|--------|------|
| onclick 함수명 | `lsReturnSearch` | `admRulReturnSearch` | `openDetail` |
| 테이블 ID | `#resultTableDiv` | `#resultAdmRulTableDiv` | `#resultTableDiv` |
| 페이지 함수 | `pageSearch('lsListDiv')` | `pageSearch('admRulListDiv')` | `pageSearch('lsListDiv')` |
| URL 추출 방식 | 파라미터 파싱 | 파라미터 파싱 | 상대경로 직접 제공 |

**문제:**
- 각 scraper의 `list_scraper.py`에 고정된 선택자/함수명 사용
- Flow 레벨에서는 이 차이를 **전혀 알 수 없음**
- 만약 페이지 구조 변경되면 **각각 따로 수정 필요**

---

### 3️⃣ **문서 스크래핑 로직 차이**

| 항목 | law | adrule | case |
|------|-----|--------|------|
| 파싱 함수명 | `parse_law_html()` | `parse_law_html()` | `parse_case_law_content()` |
| 반환 구조 | 통합 스키마 | 통합 스키마 | 청크 배열 |
| 이미지 처리 | ❌ 없음 | ✅ CLOVA OCR | ❌ 없음 |
| 복잡도 | 중간 | 높음 (이미지+OCR) | 높음 (청크 분할) |

**문제:**
- `case`의 반환 구조가 `law`, `adrule`과 **다름**
- Flow는 이를 **검증하지 않음**
- MongoDB 저장 시 **예상 밖의 데이터** 들어갈 수 있음

---

### 4️⃣ **에러 처리 부재**

```python
# unified_scraper_flow.py (Line 165-169)
async def scrape_with_semaphore(url):
    async with semaphore:
        url_str = url["url"] if isinstance(url, dict) else url
        return await scrape_document_task(
            scraper_type=scraper_type,
            doc_info={"url": url_str, "dept_code": config.dept_code}
        )
```

**문제:**
- ❌ URL 형식 검증 없음
- ❌ dept_code가 실제로 유효한지 확인 안 함
- ❌ adrule 이미지 처리 실패 시 Flow에서 감지 어려움

---

## ✅ 현재 구조가 작동하는 이유

### 다행히도...
```python
# adrule의 fetch_urls()와 case의 fetch_urls()가 
# 같은 이름으로 존재하고 있어서 현재는 작동함

# 하지만 실제 호출은 다음과 같이 됨:
# Line 47:
fetch_func_name = f"fetch_{scraper_type}_urls"  # "fetch_adrule_urls"

# 이 함수가 없으면 Line 54에서 AttributeError 발생해야 하는데...
# 아마도 실제로 호출될 때는 getattr()의 폴백 처리가 있거나,
# 또는 직접 fetch_urls()를 호출하는 다른 경로가 있을 수 있음
```

---

## 🎯 개선 방안 (권장)

### Option 1: **완전 통합 (권장) ⭐⭐⭐**
모든 scraper를 정말 동일한 인터페이스로 만들기

```python
# 모든 scraper의 list_scraper.py
async def fetch_urls(start_url, max_pages_arg):
    """통일된 함수명"""
    # 각 scraper별 구현
    pass

# 모든 scraper의 scraper.py
def scrape_and_save(url, output_dir, output_name, dept_code, save_to_db=True):
    """통일된 함수명, 통일된 반환값"""
    return {
        "status": "success",
        "doc_id": "...",
        "content": "..."
    }
```

**장점:**
- ✅ Flow 간단함
- ✅ 새 scraper 추가 쉬움
- ✅ 유지보수 쉬움

**단점:**
- ⏱️ 각 scraper 코드 리팩토링 필요

---

### Option 2: **조건부 Flow (현재 상태 개선)**
각 scraper별로 다른 로직을 Flow에서 처리

```python
@flow(name="unified-scraper")
async def unified_scraper_flow(scraper_type="law", ...):
    
    if scraper_type == "law":
        urls = await fetch_law_urls(...)
    elif scraper_type in ["adrule", "case"]:
        urls = await fetch_urls(...)  # 다른 함수명
    else:
        raise ValueError(f"Unknown scraper: {scraper_type}")
    
    # 이후 병렬 처리...
```

**장점:**
- ⏱️ 빠르게 구현 가능
- ✅ 각 scraper의 특수성 보존

**단점:**
- ❌ Flow가 복잡해짐
- ❌ 새 scraper마다 조건 추가 필요
- ❌ 유지보수 어려움

---

### Option 3: **추상 클래스 기반 (가장 깔끔)**
Scraper 인터페이스 정의

```python
# scripts/core/scraper_base.py
from abc import ABC, abstractmethod

class BaseScraper(ABC):
    @abstractmethod
    async def fetch_urls(self, start_url, max_pages):
        pass
    
    @abstractmethod
    async def scrape_and_save(self, url, output_dir, ...):
        pass

# scripts/law/logic/scraper.py
class LawScraper(BaseScraper):
    async def fetch_urls(self, start_url, max_pages):
        # law 구현
        pass
    
    async def scrape_and_save(self, url, output_dir, ...):
        # law 구현
        pass

# scripts/adrule/logic/scraper.py
class AdRuleScraper(BaseScraper):
    # adrule 구현...

# unified_scraper_flow.py
SCRAPERS = {
    'law': LawScraper(),
    'adrule': AdRuleScraper(),
    'case': CaseScraper(),
}

@flow(name="unified-scraper")
async def unified_scraper_flow(scraper_type="law", ...):
    scraper = SCRAPERS[scraper_type]
    urls = await scraper.fetch_urls(...)
    # 이후 병렬 처리...
```

**장점:**
- ✅ 깔끔한 인터페이스
- ✅ 각 scraper의 특수성 유지
- ✅ 타입 안정성 (IDE 지원)
- ✅ 쉬운 테스트

**단점:**
- ⏱️ 리팩토링 필요

---

## 📊 현재 구조 평가

### Prefect 사용에 적절한가?

| 항목 | 평가 | 설명 |
|------|------|------|
| **작업 분리** | ⭐⭐⭐ | URL 수집 → 병렬 스크래핑 → MongoDB 저장 구조 좋음 |
| **에러 처리** | ⭐ | 각 scraper별 에러 처리가 분산됨 |
| **모니터링** | ⭐⭐ | 각 Task별 로그 있지만, scraper 내부 로직은 숨겨짐 |
| **확장성** | ⭐⭐ | 새 scraper 추가 시 명확한 패턴 부재 |
| **인터페이스 통일** | ⭐ | 함수명, 반환값 불일치 |
| **테스트 용이성** | ⭐⭐ | Flow는 테스트 가능하지만, scraper 로직은 어려움 |

---

## 🔧 즉시 개선 가능한 부분

### 1. 함수명 통일
```python
# 모든 scraper의 list_scraper.py에서
async def fetch_urls(start_url, max_pages_arg):  # 통일된 이름
    pass
```

### 2. Flow에서 Scraper 동적 로딩 개선
```python
# unified_scraper_flow.py
scraper_module = import_module(f"scripts.{scraper_type}.logic.list_scraper")
fetch_urls = getattr(scraper_module, "fetch_urls")  # 직접 호출
```

### 3. 반환 값 스키마 검증
```python
from pydantic import BaseModel

class URLCollection(BaseModel):
    urls: list[dict]
    count: int

@task
async def fetch_urls_task(...) -> URLCollection:
    # 스키마 검증
    pass
```

---

## 결론

**현재 구조는 "작동하지만 취약한" 상태입니다.**

- ✅ **기본 로직은 좋음** (Task 분리, 병렬 처리)
- ❌ **인터페이스 불일치** (함수명, 반환값)
- ❌ **에러 처리 분산** (각 scraper에 숨겨짐)
- ❌ **확장성 낮음** (새 scraper 추가 어려움)

**권장 우선순위:**
1. **즉시**: 함수명 통일 (20분)
2. **단기**: 반환 값 스키마 정의 (1시간)
3. **중기**: 추상 클래스 기반 리팩토링 (하루)

