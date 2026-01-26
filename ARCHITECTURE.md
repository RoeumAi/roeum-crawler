# 🏗️ 프로덕션급 구조 - 설명서

## 📋 개요

이 프로젝트는 **4개의 주요 법령 관련 정보**를 크롤링하는 시스템입니다:

| Scraper | 설명 | Collection |
|---------|------|-----------|
| **law** | 법령 | law |
| **adrule** | 행정규칙 | adrule |
| **case** | 판례 | case |
| **decision** | 심의결정례 | decision |
| **interpretation** | 해석례 | interpretation |
| **mediation_case** | 조정사건례 | mediation_case |

---

## 🗂️ 구조

### 이전 (혼란스러운 상태)
```
run_initial_crawl.py      ❌ law용만 있음
run_prefect.py            ❌ 관리 안 됨
prefect_deploy.py         ❌ 중복
```

### 현재 (체계적)
```
crawl.py                  ✅ 모든 scraper 통합 실행
deploy.py                 ✅ Deployment 자동 관리

scripts/core/
├── config.py             ✅ 중앙 설정 (모든 scraper 설정 한 곳에서 관리)
├── flows/
│   ├── law_scraper_flow.py      (레거시 - 곧 삭제 예정)
│   └── unified_scraper_flow.py  ✅ 새 통합 Flow (모든 scraper용)
└── database/
    └── unified_repository.py    (공통)

scripts/law/
├── logic/
│   ├── list_scraper.py
│   └── scraper.py
└── (설정 파일 추가 가능)

scripts/adrule/
├── logic/
│   ├── list_scraper.py
│   └── scraper.py
└── (다른 scraper들도 동일 구조)
```

---

## 🎯 사용법

### 1️⃣ 크롤링 실행

#### 모든 scraper 실행
```bash
python3 crawl.py
```

#### 특정 scraper만 실행
```bash
python3 crawl.py --scraper law

# 여러 개 선택
python3 crawl.py --scraper law adrule case
```

#### 옵션
```bash
# 동시 수 변경 (기본값: 3)
python3 crawl.py --concurrent 5

# 페이지 수 제한 (테스트용)
python3 crawl.py --pages 1

# 조합
python3 crawl.py --scraper law --concurrent 2 --pages 5
```

#### 사용 가능한 scraper 확인
```bash
python3 crawl.py --list
```

---

### 2️⃣ Prefect 자동 실행 설정

#### Step 1: Prefect 서버 시작
```bash
prefect server start
```
(터미널 A에서 계속 실행)

#### Step 2: Work pool 생성 (1회만)
```bash
prefect work-pool create --type process default
```

#### Step 3: Deployment 생성
```bash
python3 deploy.py
```

#### Step 4: Worker 시작
```bash
prefect worker start --pool default
```
(터미널 C에서 계속 실행)

---

## ⚙️ 설정 수정

### 중앙 설정 (`scripts/core/config.py`)

모든 scraper의 설정을 한 곳에서 관리합니다:

```python
SCRAPERS = {
    'law': ScraperConfig(
        name='law',
        display_name='법령',
        dept_code='1492000',
        collection_name='law',
        max_pages=None,              # ← 여기서 변경
        max_concurrent=3,            # ← 여기서 변경
    ),
    # ... 다른 scraper들
}
```

### 개별 scraper 고유 설정 (예: `scripts/law/config.py`)

각 scraper만의 고유 설정이 필요하면:

```python
# scripts/law/config.py
LAW_SPECIFIC_SETTINGS = {
    'list_page_url': 'https://www.law.go.kr/LSW/lsAstSc.do?...',
    'timeout': 60,
    # ...
}
```

---

## 📊 결과 확인

### MongoDB에 저장된 데이터 확인
```bash
python3 << 'EOF'
from scripts.core.database.mongo_client import MongoClientSingleton

client = MongoClientSingleton()
db = client._db

# 각 컬렉션별 문서 수
for scraper in ['law', 'adrule', 'case', 'decision', 'interpretation', 'mediation_case']:
    count = db[scraper].count_documents({})
    print(f"{scraper}: {count}개")

# 최근 저장된 문서들
print("\n📄 최근 저장된 법령:")
docs = list(db['law'].find().sort('created_at', -1).limit(3))
for doc in docs:
    print(f"  - {doc.get('title')}")
EOF
```

---

## 🔄 Flow 구조

모든 scraper는 **동일한 Flow**를 사용합니다:

```
[STEP 1] URL 수집
   ↓
[STEP 2] 병렬 스크래핑
   ↓
[STEP 3] MongoDB 저장
   ↓
[완료]
```

### 코드 위치
- Flow: `scripts/core/flows/unified_scraper_flow.py`
- 실행 스크립트: `crawl.py`
- 설정: `scripts/core/config.py`

---

## 🐛 문제 해결

### Q: "scraper not found" 에러
**답**: `scripts/core/config.py`에 scraper를 추가했는지 확인

```python
SCRAPERS = {
    'my_new_scraper': ScraperConfig(...),  # ← 이렇게 추가
}
```

### Q: Prefect deployment가 작동 안 함
**답**: Worker가 실행 중인지 확인

```bash
prefect worker start --pool default
```

### Q: 특정 scraper만 실행 중단하고 싶음
**답**: 설정에서 비활성화

```python
# scripts/core/config.py
SCRAPERS = {
    'law': ScraperConfig(...),     # ✅ 활성
    # 'adrule': ScraperConfig(...),  # ❌ 비활성 (주석 처리)
}
```

---

## 📈 다음 단계

### 개선 예정
1. ✅ 통합 Flow 구현 (완료)
2. ✅ 중앙 설정 관리 (완료)
3. ⏳ 각 scraper의 `config.py` 개별 생성
4. ⏳ 에러 모니터링 대시보드
5. ⏳ 로그 중앙화 (ELK Stack 등)
6. ⏳ 성능 최적화

---

## 🎓 추가 정보

### Prefect CLI 명령어
```bash
# Deployment 목록
prefect deployment ls

# 특정 deployment 실행
prefect deployment run 'law-scraper:deployment'

# Worker 상태
prefect worker ls
```

### MongoDB 쿼리
```bash
# 모든 컬렉션 확인
use original_db
show collections

# 문서 개수
db.law.count()

# 최근 문서
db.law.findOne({}, {sort: {created_at: -1}})
```

---

## 📞 참고

- **Prefect 문서**: https://docs.prefect.io
- **MongoDB 문서**: https://docs.mongodb.com
- **프로젝트 구조**: 이 문서 참고
