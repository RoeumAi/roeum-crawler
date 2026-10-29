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
| `pdf-reparse` | 기본=미리보기 | 이미 넣은 PDF 문서를 지금 파서로 다시 파싱(아래 "PDF 다시 파싱") |
| `csv-import` | 기본=미리보기 | 판례·행정해석·판정사례 CSV 넣기(10-25 완료). `python3 manual.py csv-import case --csv <파일>` → `--apply` |

`pdf-ingest`는 DB에 이미 있는 파일(`metadata.source_file`)은 건너뛴다. 이미 넣은 문서를 고치려면 `pdf-reparse`를 쓴다.

#### PDF 다시 파싱 (`pdf-reparse`)

맥미니(원본 PDF가 `~/loum/pdf_db`에 있음)에서 실행한다. 파서는 `CHAT_GENERATION_URL`의 chat_generation이다.

```bash
python3 manual.py pdf-reparse --type 행정해석 --limit 20     # 미리 보기: 상태·글자 보존율·표 수 변화
python3 manual.py pdf-reparse --doc-id <doc_id>              # 한 건만
python3 manual.py pdf-reparse --apply                        # 지난 파싱이 불완전한 문서 전부 저장
python3 manual.py pdf-reparse --include-complete --apply     # complete 였던 문서까지(표 인식 개선 반영)
```

- 대상: `metadata.source_type=pdf` + `metadata.pdf_retry_needed=true`(지난 파싱이 partial).
- 문서·청크 ID와 청크 수는 그대로, 본문만 바꾼다. 새로 읽은 글이 없거나 35% 넘게 줄면 기존 문서를 유지한다. 한 문서는 트랜잭션 하나로 바꾼다.
- 임베딩: 기존 벡터는 남겨 둔다. 판례·행정해석은 대표 행의 `embedding_hash`를 지워 맥미니 03:30 `mongo_embed_refresh`가 새 본문으로 다시 임베딩한다. 결정례는 그 작업의 대상 컬렉션이 아니므로 다시 임베딩하려면 그 작업에 `decision`을 넣어 돌린다.
- OCR이 필요한 스캔 PDF는 chat_generation이 OpenAI OCR을 부른다(10-28 시험: 문서당 약 $0.002~0.013).

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
| `fix:rescrape-paragraphs` | 기본=미리보기 | 문단 없이 한 줄로 저장된 행정심판재결례·헌재결정례·법제처해석례·판례(웹 수집분)를 원문에서 다시 받아 문단을 살린다. `--collection admin_decc --limit 20`으로 먼저 확인 |
| `fix:split-paragraphs` | 기본=미리보기 | 원문을 다시 받을 수 없는 한 줄 본문(CSV로 넣은 판례 등)을 판결문 번호(1. 가. 1) 가) (1) ○) 앞에서 문단으로 나눈다. 공백만 빈 줄로 바꿔 임베딩은 그대로. `--collection case --samples 5`로 먼저 확인 |
| `fix:text-cleanup` | 기본=미리보기 | 한글 사이 `？`→`ㆍ`, `<br>`→줄바꿈, `&nbsp;`→공백, 법령 `제N조(제목)` 뒤 공백→줄바꿈. 바뀐 행은 `content_hash`·`source_version_id` 재계산, 글자가 바뀐 판례·행정해석은 대표 행 `embedding_hash`를 지워 재임베딩(공백만 바뀌면 그대로). `--collections case law --samples 3`로 먼저 확인 |
| `fix:retry-judgment-pdf` | 기본=미리보기 | PDF 본문 없는 주요판정사례 첨부 재추출 |

## 새 도구를 만들 때

1. `argparse`로 인자를 받고 `if __name__ == "__main__":` 아래에서만 실행한다(`--help`가 실행 없이 떠야 한다).
2. DB에 쓰는 도구는 미리 보기를 기본으로 하고 `--apply`로 쓰게 만든다.
3. `manual.py`의 `TASKS`와 이 문서에 한 줄 추가한다. `tests/test_manual_tasks.py`가 모든 작업의 `--help`를 확인한다.

## 파싱이 제대로 안 된 문서 다루기

| 증상 | 어떻게 드러나나 | 처리 |
|---|---|---|
| 일일 수집 중 문서 파싱 실패 | 일일 요약 `[ERROR]`·배치 관리 PARTIAL, `failedTargets` | 다음 실행이 자동으로 다시 시도한다(신규는 DB에 없어서, 법령·행정규칙 일부 저장은 `metadata.save_incomplete`, 판정·조정 첨부는 `pdf_retry_needed`, 본문 없이 메타데이터만 남은 문서는 매일 재확인). 같은 문서가 며칠째 실패하면 `scrape:<종류> <URL> --no-db`로 원인을 본다 |
| 수동으로 넣은 PDF가 일부 페이지만 읽힘 | `metadata.pdf_extraction_status=partial`, `pdf_retry_needed=true` | `pdf-reparse`로 지금 파서로 다시 읽는다(미리 보기 → `--apply`). 그래도 partial 이면 스캔 상태가 나쁜 원본이다 → 원본 파일을 다시 구하거나 그대로 둔다(읽은 부분은 검색됨) |
| 별표·첨부를 못 읽음 | 문서 실패 + `save_incomplete` | 다음 실행이 다시 받는다. 계속 실패하면 Open API 응답(`target=law&MST=` / `target=admrul&ID=`)의 별표 링크를 확인 |
| 파서 자체 문제(표가 깨짐 등) | 사람이 화면에서 발견 | chat_generation 파서 담당자에게 원본 파일과 함께 전달. 고쳐진 뒤 `pdf-reparse --include-complete`(수동 PDF) 또는 해당 문서에 `save_incomplete` 표시(크롤링 문서) |
