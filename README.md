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
run_daily.sh (일일 실행, ECS 진입점 docker/entrypoint.sh 가 호출)
  ├─ crawl.py --mode update --result-json …   # 스크래퍼 10개를 스레드로 병렬 실행
  │    └─ scripts/<스크래퍼>/logic/{list_scraper,scraper}.py
  │         └─ scripts/core/database/unified_repository.py → MongoDB original_db
  ├─ scripts/core/flows/refresh_current_status_flow.py   # law/adrule 현재 시행 버전 재계산
  └─ scripts/report/batch_result.py                       # 마지막 줄 BATCH_RESULT + 종료코드
```

- 이미 수집했는지는 전부 DB로 판단한다(`data/`·`logs/`는 쓰기 전용이며 git 에 올리지 않는다). PDF·HWP 본문은 전부 chat_generation `/api/extract-text`(`CHAT_GENERATION_URL`)로 추출한다(`scripts/utils/document_extraction.py`). 표는 `document_blocks`의 칸 구조로 Markdown 표를 만든다(`scripts/utils/document_blocks.py`). 크롤러 안에서 OCR·PDF 파싱을 따로 하지 않는다.
- 법령·행정규칙 별표는 본문 HTML에 없어 국가법령 Open API의 별표 원본 파일로 읽어 `article_number="별표N"` 행으로 저장한다(`scripts/core/lawgokr_annex.py`). 행정규칙 PDF 뷰어 문서도 뷰어의 원본 파일을 받아 같은 파서로 읽는다.
- **요청 재시도**: 목록·상세 페이지 요청은 `scripts/core/run_report.py` 의 `call_with_retry`(동기)·`call_with_retry_async`(비동기)를 쓴다. 목록 페이지는 5·10초 간격 3회, 어떤 예외든 다시 시도한다(Open API 가 잠깐 404 를 주기도 한다). 상세 페이지는 `retry_detail_fetch(_async)` 로 같은 간격·횟수지만 네트워크 오류·시간 초과·5xx 만 다시 시도하고(4xx·파싱 오류는 바로 실패), 문서 하나당 90초·스크래퍼 한 번 실행당 총 900초(스레드 시간)를 넘기면 더 시도하지 않아 일일 실행 상한(170분)을 늘리지 않는다. 소진되면 마지막 예외가 그대로 올라가 예전과 같은 실패 결과가 된다.
- **첨부 추출 재시도 상한**: 주요판정사례·조정사건례는 첨부 추출이 3회 연속 실패하면 `metadata.pdf_retry_needed` 를 끄고(옛 본문 유지, `pdf_retry_count`·`pdf_last_attempt_*` 는 남김) 더는 매일 밤 다시 추출하지 않는다.
- **결과 보고 규칙**: 실패를 경고 로그로만 남기지 않는다. 목록 페이지 실패는 `scripts/core/run_report.record_failure()`로, 문서 실패는 `scrape_and_save`가 `status: "failed"`를 돌려줘서 `failedTargets`에 잡히게 한다. 종료코드: 0(끝까지 돎, failed>0이면 부분 실패) / 2(재시도 후에도 목록 전체 실패) / 1(비정상 종료·재계산 실패). 목록 수집 밖에서 난 예상 밖 오류(DB 인증 실패·코드 오류 등)는 결과에 `error_kind: "unexpected"`·`failedTargets: <스크래퍼>:run` 으로 남고 목록 실패가 아니므로 재시도(2회×15분) 없이 0(부분 실패)로 보고한다. 문서 실패 원인 중 분류되는 것(추출 서버·Open API·시간 초과)은 `run_report.note_reason`으로 세어 일일 요약 '실패 원인' 줄에 들어가고, 나머지는 '기타 저장 실패'다. 일부 섹션만 저장된 문서(해석례·판례·헌재·법제처·행정심판)는 `metadata.save_incomplete` 표시로 update 모드가 다시 수집한다.
- 운영 실행 위치: ECS Fargate ARM64 예약 실행 `roeum-crawler-daily`(매일 00:00 KST, `Dockerfile`, `ecs/task-definition.json`, `.github/workflows/crawler-image.yml`). 맥미니 launchd 는 꺼 두었다(`run_daily.sh` 의 맥미니 분기는 로컬 실행용으로만 남아 있다). 시작·종료는 `ops/batch_report.py`(원본: roeum-manage-server `tools/batch-report/`)가 배치 관리 시스템에 보고한다. 설계: 상위 작업 공간 `docs/crawler-ecs-migration-design-20261025.md`.

## 수동 실행 도구

일일 실행 밖에서 따로 돌리는 작업(지정 문서 재수집, PDF 넣기·다시 파싱, 문단·텍스트 정비)은 `python3 manual.py`로 목록을 보고 실행한다. 운영 DB에 쓰는 작업은 미리 보기 후 `--yes`가 필요하다. 설명: `docs/manual-tasks.md`.

## 새 스크래퍼 추가

1. `scripts/core/config.py`의 `SCRAPERS`에 `ScraperConfig` 추가
2. `scripts/<이름>/logic/list_scraper.py`(`fetch_urls`)와 `scraper.py`(`scrape_and_save`) 작성 — 실패 보고 규칙을 지킨다
3. 같은 `ScraperConfig` 항목에 목록 참조 URL(`list_page_url`)·update 방식(`update_strategy`)·재수집 필터(`retry_filter`, `retry_save_incomplete`)를 채운다 — `crawl.py` 는 고치지 않는다

## 환경 변수

전체 목록과 설명은 `.env.example`이 정본이다. 실제 값은 `.env`(git 추적 안 함)나 ECS 태스크 정의에 둔다. 필수는 `MONGODB_URI`뿐이고, PDF 추출(`CHAT_GENERATION_URL`)·Discord 알림·배치 보고(`BATCH_REPORT_*`)·`LAWGO_OC`(비우면 코드 기본값)가 선택이다.

## 저장소 위생

- 공개 저장소다. 접속 주소·키·웹훅·내부 IP·개인 계정을 코드, 문서, 테스트에 적지 않는다.
- 새 소스 파일이 조용히 무시되지 않게 `.gitignore`는 좁은 패턴만 둔다. 임시 분석 스크립트는 `scratch/`에 둔다.
- 일회성 정비 도구는 끝나면 삭제한다(이력은 git에 남는다). `scripts/migrations/`에는 아직 진행 중인 것만 둔다.
