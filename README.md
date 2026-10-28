# Roeum Crawler

노동 관련 법령·행정규칙·판례·해석례 등 10종을 매일 수집해 MongoDB `original_db`에 저장한다. 저장된 문서는 별도 임베딩 작업을 거쳐 AI 상담 검색(RAG)에 쓰인다.

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium        # 헤드리스 브라우저 (최초 1회)

python3 crawl.py --list                                   # 스크래퍼 목록
python3 crawl.py --mode update --scraper law adrule      # 증분 수집(DB에 있는 문서는 건너뜀)
python3 crawl.py --pages 1 --concurrent 1 --scraper case # 시험 실행
./run_daily.sh                                            # 일일 실행(수집 → 시행 버전 재계산 → BATCH_RESULT)

# 테스트 — 운영 DB에 닿지 않게 가짜 로컬 주소로
MONGODB_URI=mongodb://127.0.0.1:1/x CRAWLER_ENV=local python -m pytest tests -q
```

⚠️ 크롤러는 **운영** MongoDB `original_db`에 쓴다. 실제 수집 실행은 승인된 경로(일일 예약)로만 한다.

## 스크래퍼

`scripts/core/config.py`의 `SCRAPERS`에 정의(10개): `law` 법령, `adrule` 행정규칙, `case` 판례, `decision` 심의결정례, `interpretation` 해석례, `mediation_case` 조정사건례, `judgment` 주요판정사례, `constitutional_decc` 헌재결정례, `legislation_expc` 법제처해석례, `admin_decc` 행정심판재결례. 컬렉션 이름은 키와 같다.

## 구조

```
run_daily.sh (일일 실행, 맥미니 launchd 또는 ECS 진입점 docker/entrypoint.sh)
  ├─ crawl.py --mode update --result-json …   # 스크래퍼 10개를 스레드로 병렬 실행
  │    └─ scripts/<스크래퍼>/logic/{list_scraper,scraper}.py
  │         └─ scripts/core/database/unified_repository.py → MongoDB original_db
  ├─ scripts/core/flows/refresh_current_status_flow.py   # law/adrule 현재 시행 버전 재계산
  └─ scripts/report/batch_result.py                       # 마지막 줄 BATCH_RESULT + 종료코드
```

- 이미 수집했는지는 전부 DB로 판단한다(`data/`·`logs/`는 쓰기 전용). PDF·HWP 본문은 전부 chat_generation `/api/extract-text`(`CHAT_GENERATION_URL`)로 추출한다(`scripts/utils/document_extraction.py`). 표는 `document_blocks`의 칸 구조로 Markdown 표를 만든다(`scripts/utils/document_blocks.py`). 크롤러 안에서 OCR·PDF 파싱을 따로 하지 않는다.
- 법령·행정규칙 별표는 본문 HTML에 없어 국가법령 Open API의 별표 원본 파일로 읽어 `article_number="별표N"` 행으로 저장한다(`scripts/core/lawgokr_annex.py`). 행정규칙 PDF 뷰어 문서도 뷰어의 원본 파일을 받아 같은 파서로 읽는다.
- **결과 보고 규칙**: 실패를 경고 로그로만 남기지 않는다. 목록 페이지 실패는 `scripts/core/run_report.record_failure()`로, 문서 실패는 `scrape_and_save`가 `status: "failed"`를 돌려줘서 `failedTargets`에 잡히게 한다. 종료코드: 0(끝까지 돎, failed>0이면 부분 실패) / 2(재시도 후에도 목록 전체 실패) / 1(비정상 종료·재계산 실패).
- 운영 실행 위치: 맥미니 launchd → ECS Fargate ARM64 예약 실행으로 전환 중(`Dockerfile`, `ecs/task-definition.json`, `.github/workflows/crawler-image.yml`). 설계: 상위 작업 공간 `docs/crawler-ecs-migration-design-20261025.md`.

## 수동 실행 도구

일일 실행 밖에서 따로 돌리는 작업(지정 문서 재수집, PDF·CSV 넣기, 데이터 정비)은 `python3 manual.py`로 목록을 보고 실행한다. 운영 DB에 쓰는 작업은 미리 보기 후 `--yes`가 필요하다. 설명: `docs/manual-tasks.md`.

## 새 스크래퍼 추가

1. `scripts/core/config.py`의 `SCRAPERS`에 `ScraperConfig` 추가
2. `scripts/<이름>/logic/list_scraper.py`(`fetch_urls`)와 `scraper.py`(`scrape_and_save`) 작성 — 실패 보고 규칙을 지킨다
3. `crawl.py`의 `get_list_page_url`과 update 모드 필터 분기에 추가

## 환경 변수

`.env.example` 참고. `MONGODB_URI`(필수), `CHAT_GENERATION_URL`, `SAVE_JSONL`, `CRAWLER_LOG_STDOUT`, `DISCORD_WEBHOOK`(·`DISCORD_WEBHOOK_DATA_COLLECT`, 일일 요약), `CRAWLER_DISCORD_SUMMARY`, `CRAWLER_LOG_LOCATION`, `LAWGO_OC`.
