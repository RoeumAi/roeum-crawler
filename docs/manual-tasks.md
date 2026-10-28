# 수동 실행 도구

일일 크롤링(`run_daily.sh`, ECS 예약 00:00)과 별개로 사람이 따로 돌리는 작업이다. 진입점은 하나다.

```bash
python3 manual.py                     # 목록과 안전 등급
python3 manual.py <작업> --help       # 작업별 인자 (실행하지 않음)
python3 manual.py <작업> [인자...]    # 실행
```

## 실행 위치·환경

- 크롤러 저장소를 받은 곳(맥미니 `~/loum/roeum-crawler` 또는 로컬)에서 `pip install -r requirements.txt` 후 실행한다.
- `.env`의 `MONGODB_URI`가 **운영** `original_db`를 가리킨다. 확인만 할 때는 미리 보기 옵션을 쓴다.
- PDF 추출이 필요한 작업(`pdf-ingest`, `fix:retry-judgment-pdf`, `scrape:judgment`)은 `CHAT_GENERATION_URL`의 `/api/extract-text`를 부른다. 맥미니 기본값은 staging chat_generation(`127.0.0.1:8000`)이다. 운영과 같은 파서로 넣으려면 운영 chat_generation과 같은 버전인지 먼저 확인한다.
- 일일 실행(00:00~00:30 KST)과 겹치지 않게 낮에 돌린다.

## 안전 등급

| 등급 | 뜻 | 쓰는 법 |
|---|---|---|
| 조회만 | 사이트·DB를 읽기만 한다 | 바로 실행 |
| 기본=미리보기 | 인자 없이 돌리면 바꿀 내용만 보여 준다 | 확인 후 `--apply`(또는 `--execute`) |
| 기본=쓰기 | 인자 없이 돌리면 운영 DB에 쓰거나 지운다 | 미리 보기(`--dry-run`/`--no-db`)로 확인 → `--yes`를 붙여 실행. `--yes`가 없으면 `manual.py`가 막는다 |

## 작업 목록

### 목록·URL 점검 (조회만)
| 작업 | 하는 일 | 예 |
|---|---|---|
| `list:adrule` · `list:case` · `list:judgment` | 목록 URL을 JSONL로 뽑는다 | `python3 manual.py list:judgment -p 2 -o /tmp/j.jsonl` |
| `check-url:law` · `check-url:adrule` · `check-url:case` · `check-url:interpretation` | 목록·상세 페이지가 정상으로 열리는지 | `python3 manual.py check-url:law "<목록 URL>"` |

### 지정 문서 수집 (기본=쓰기)
| 작업 | 하는 일 | 예 |
|---|---|---|
| `scrape:law` · `scrape:adrule` · `scrape:case` · `scrape:judgment` | 상세 URL 하나를 수집해 저장 | 미리 보기 `python3 manual.py scrape:law "<URL>" -d 1492000 -o test --no-db` → 저장 `… --yes` |
| `cherry-pick:law` | `TARGET_LAWS`(파일 안 목록)의 법령을 찾아 수집 | `python3 manual.py cherry-pick:law "근로기준법" --yes` |
| `cherry-pick:case` | ALL_DB 크롤링 가능 판례 목록 수집 | `python3 manual.py cherry-pick:case --resume 0 --yes` |

여러 건을 한꺼번에 다시 받을 때는 수동 도구 대신 일일 실행과 같은 경로를 쓴다: `python3 crawl.py --mode update --scraper <이름>` (DB에 있는 문서는 건너뜀, 실패는 결과에 남음).

### 자료 넣기
| 작업 | 등급 | 하는 일 |
|---|---|---|
| `pdf-ingest` | 기본=쓰기 | 맥미니 `~/loum/pdf_db`의 판례·행정해석·결정례 PDF를 chat_generation으로 파싱해 넣는다. `--type 판례 --dry-run`으로 먼저 확인 |
| `csv-import` | 기본=미리보기 | 판례·행정해석·판정사례 CSV 넣기(10-25 완료). `python3 manual.py csv-import case --csv <파일>` → `--apply` |

`scripts/pdf_ingest/reparse_existing.py`는 실행기가 아니라 함수 모음이다(이미 넣은 PDF 문서를 같은 ID로 다시 파싱하는 교체본 계산). 이를 불러 임베딩까지 붙여 트랜잭션으로 바꾸는 실행기는 아직 없다.

### 데이터 정비 (대부분 끝난 일회성 — 같은 문제가 다시 생겼을 때만)
| 작업 | 등급 | 하는 일 |
|---|---|---|
| `fix:clean-pdf-artifacts` | 기본=미리보기 | PDF·인쇄 흔적 제거 |
| `fix:judgment-case-numbers` | 기본=미리보기 | 주요판정사례 빈 사건번호 복구 |
| `fix:source-provenance` | 기본=미리보기 | 인용 출처 필드 점검·보강 |
| `fix:stable-ids` | 기본=미리보기(`--execute`) | 법령·행정규칙 안정 ID 정규화 |
| `fix:chunk-identifiers` | 기본=쓰기 | 청크 식별자 보강 |
| `fix:constitutional-sub-titles` | 기본=미리보기 | 헌재결정례 sub_title 복구 |
| `fix:reference-sub-titles` | 기본=미리보기 | 웹 참조용 sub_title 보강 |
| `fix:prune-interpretation` | 기본=쓰기(**삭제**) | 해석례를 고용노동부 관련만 남김 |
| `fix:prune-law` | 기본=쓰기(**삭제**) | 법령을 고용노동부 소관만 남김 |
| `fix:retry-judgment-pdf` | 기본=미리보기 | PDF 본문 없는 주요판정사례 첨부 재추출 |

## 새 도구를 만들 때

1. `argparse`로 인자를 받고 `if __name__ == "__main__":` 아래에서만 실행한다(`--help`가 실행 없이 떠야 한다).
2. DB에 쓰는 도구는 미리 보기를 기본으로 하고 `--apply`로 쓰게 만든다.
3. `manual.py`의 `TASKS`와 이 문서에 한 줄 추가한다. `tests/test_manual_tasks.py`가 모든 작업의 `--help`를 확인한다.
