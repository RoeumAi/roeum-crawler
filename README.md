# 🚀 Roeum Crawler - 법령 정보 통합 크롤링 시스템

법령, 행정규칙, 판례 등 여러 유형의 법령 관련 정보를 체계적으로 크롤링하고 MongoDB에 저장하는 시스템입니다.

## 📋 지원 Scraper

| 이름 | 설명 | 컬렉션 |
|------|------|--------|
| **law** | 법령 | law |
| **adrule** | 행정규칙 | adrule |
| **case** | 판례 | case |
| **decision** | 심의결정례 | decision |
| **interpretation** | 해석례 | interpretation |
| **mediation_case** | 조정사건례 | mediation_case |

## 🏗️ 시스템 아키텍처

```
crawl.py (사용자 인터페이스)
    ↓
unified_scraper_flow.py (제네릭 Flow)
    ↓
개별 scraper (list_scraper.py, scraper.py)
    ↓
UnifiedDocumentRepository (MongoDB)
    ↓
MongoDB (original_db - 6개 컬렉션)
```

자세한 내용은 **[ARCHITECTURE.md](ARCHITECTURE.md)** 참고

## 🚀 빠른 시작

### 1️⃣ 크롤링 실행

#### 모든 scraper 실행
```bash
python3 crawl.py
```

#### 특정 scraper만 실행
```bash
python3 crawl.py --scraper law
python3 crawl.py --scraper law adrule case
```

#### 옵션 (동시 수, 페이지 수 등)
```bash
# 동시 크롤링 수 조정
python3 crawl.py --concurrent 5

# 페이지 수 제한 (테스트용)
python3 crawl.py --pages 1

# 조합
python3 crawl.py --scraper law --concurrent 2 --pages 5

# 사용 가능한 scraper 확인
python3 crawl.py --list
```

### 2️⃣ Prefect 자동 실행 설정

#### Step 1: Prefect 서버 시작
```bash
prefect server start
```

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

**이후 매주 월요일 09:00에 자동 실행됩니다.**

## 📊 결과 확인

### MongoDB 데이터 확인
```bash
python3 << 'EOF'
from scripts.core.database.mongo_client import MongoClientSingleton

client = MongoClientSingleton()
db = client._db

# 각 컬렉션의 문서 수
for scraper in ['law', 'adrule', 'case', 'decision', 'interpretation', 'mediation_case']:
    count = db[scraper].count_documents({})
    print(f"{scraper}: {count}개")

# 최근 저장된 문서
print("\n📄 최근 저장된 법령:")
docs = list(db['law'].find().sort('created_at', -1).limit(3))
for doc in docs:
    print(f"  - {doc.get('title')}")
EOF
```

## ⚙️ 설정 수정

### 중앙 설정 - `scripts/core/config.py`

모든 scraper의 설정을 한 곳에서 관리합니다:

```python
SCRAPERS = {
    'law': ScraperConfig(
        name='law',
        display_name='법령',
        dept_code='1492000',
        collection_name='law',
        max_pages=None,              # 여기서 변경
        max_concurrent=3,            # 여기서 변경
    ),
    # ... 다른 scraper들
}
```

## 📁 프로젝트 구조

```
roeum-crawler/
├── crawl.py                      # 🎯 크롤링 메인 스크립트
├── deploy.py                     # 📦 Deployment 관리
├── ARCHITECTURE.md               # 📚 상세 가이드
├── README.md                     # 📖 이 파일
├── requirements.txt              # 📦 의존성
│
├── scripts/
│   ├── core/
│   │   ├── config.py            # ⚙️ 중앙 설정
│   │   ├── flows/
│   │   │   └── unified_scraper_flow.py # ✅ 통합 Flow
│   │   └── database/
│   │       ├── mongo_client.py
│   │       └── unified_repository.py
│   │
│   ├── law/                      # 법령 scraper
│   ├── adrule/                   # 행정규칙 scraper
│   ├── case/                     # 판례 scraper
│   ├── decision/                 # 심의결정례 scraper
│   ├── interpretation/           # 해석례 scraper
│   └── mediation_case/           # 조정사건례 scraper
│
├── data/
│   ├── output/                   # 🔄 JSONL 출력 파일
│   ├── raw/                      # 원본 데이터
│   └── final/                    # 최종 데이터
│
├── logs/                         # 📋 로그 파일
```

## 🔧 설치 및 환경 설정

### 의존성 설치
```bash
pip install -r requirements.txt
```

### 필수 도구
- Python 3.10+
- Playwright (브라우저 자동화)

### 환경 변수 설정 (`.env`)
```
MONGO_URI=mongodb+srv://...       # MongoDB Atlas 연결 문자열
MONGO_DB_NAME=original_db         # 데이터베이스 이름
PREFECT_API_URL=                  # 로컬 모드는 비워두기
```

## 📖 상세 가이드

더 자세한 내용은 **[ARCHITECTURE.md](ARCHITECTURE.md)** 참고:

- 🗂️ 구조 설명
- 📊 Flow 구조
- 🐛 문제 해결
- 📈 다음 단계

## 💡 팁

### 빠른 테스트
```bash
# 첫 페이지만 테스트
python3 crawl.py --pages 1 --concurrent 2
```

### 특정 scraper 모니터링
```bash
# Prefect Dashboard 열기 (Prefect 서버 실행 중)
open http://localhost:4200
```

### 스케줄 변경
```bash
# 매일 자정에 실행 (CRON: 0 0 * * *)
python3 deploy.py --schedule "0 0 * * *"
```

## 🆘 문제 해결

### Q: "scraper not found" 에러
A: `scripts/core/config.py`에서 scraper가 정의되어 있는지 확인

### Q: MongoDB 연결 에러
A: `.env` 파일에서 `MONGO_URI` 설정 확인 (MongoDB Atlas 연결 문자열)

### Q: Prefect deployment 작동 안 함
A: Worker가 실행 중인지 확인: `prefect worker start --pool default`

자세한 문제 해결은 **[ARCHITECTURE.md](ARCHITECTURE.md#-문제-해결)** 참고

## 📞 연락처

문제가 있거나 기능 요청이 있으면 이슈를 생성해주세요.

---

**마지막 업데이트**: 2026-01-21


## 행정예규 파이프라인 통합 실행 (run_law_scraper.sh)

## 판례 파이프라인 통합 실행 (run_law_scraper.sh)
