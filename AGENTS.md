# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt
playwright install chromium   # 헤드리스 브라우저 설치 (최초 1회)

# 크롤링 실행
python3 crawl.py                                   # 모든 스크래퍼 실행
python3 crawl.py --scraper law adrule case        # 특정 스크래퍼만 실행
python3 crawl.py --pages 1 --concurrent 1         # 테스트 모드 (1페이지, 순차)

# Prefect 스케줄 배포 (주 1회 월요일 09:00 KST)
prefect server start
prefect work-pool create --type process default
python3 deploy.py
prefect worker start --pool default
```

## 스크래퍼 목록

`scripts/core/config.py`의 `SCRAPERS` 딕셔너리에 모두 정의:

| key | 데이터 종류 | MongoDB 컬렉션 |
|-----|------------|---------------|
| `law` | 법령 | law |
| `adrule` | 행정규칙 | adrule |
| `case` | 판례 | case |
| `decision` | 심의결정례 | decision |
| `interpretation` | 해석례 | interpretation |
| `mediation_case` | 조정사건례 | mediation_case |
| `judgment` | 주요판정사례 | judgment |

## 아키텍처

```
crawl.py (CLI 진입점)
    └─ scripts/core/flows/unified_scraper_flow.py   # Prefect Flow
         ├─ scripts/{scraper_name}/                  # 스크래퍼별 Scrapy 스파이더
         │    └─ Playwright (동적 콘텐츠 렌더링)
         └─ scripts/core/database/unified_repository.py  # MongoDB 저장
              └─ MongoDB Atlas (original_db)
```

크롤링 결과는 `data/output/`에 JSONL로 저장된 후 `data/final/`로 이동, MongoDB에 적재.

## 새 스크래퍼 추가 방법

1. `scripts/core/config.py`의 `SCRAPERS`에 `ScraperConfig` 항목 추가
2. `scripts/{new_scraper_name}/` 폴더 생성 후 기존 스크래퍼 구조 참고하여 Scrapy 스파이더 작성
3. `unified_scraper_flow.py`에서 자동으로 `SCRAPERS` 딕셔너리를 순회하므로 별도 등록 불필요

## 환경 변수

`scripts/core/config.py`의 `EnvironmentConfig`에 MongoDB URL이 하드코딩되어 있음. 운영 환경에서는 `.env` 파일로 오버라이드 필요.

```
MONGODB_URL    # MongoDB Atlas 연결 문자열
```
