# 법령/행정규칙 현재 적용 버전 추적 및 조문별 신구대조 설계

날짜: 2026-07-17

## 배경

law.go.kr은 법령/행정규칙이 개정될 때 기존 URL의 내용을 바꾸지 않고, 개정판마다 새 URL(법령일련번호/행정규칙일련번호)을 발급한다. 그 결과 title이 동일한 문서(예: 근로기준법)라도 서로 다른 `doc_id`를 가진 여러 revision이 MongoDB에 존재하며, 그중 "지금 시행 중인 것"이 무엇인지는 `metadata.effective`(시행일자)로만 구분된다.

현재 크롤러는 이 상태 판단(`is_active`/`is_upcoming`)을 최초 크롤링 시점에 한 번만 계산하고 이후 갱신하지 않는다. 시간이 지나 시행예정이었던 버전의 시행일이 도래해도, 그 URL을 다시 방문하지 않는 한 상태가 저절로 바뀌지 않는다. 또한 문서 변경 시 남기는 `update_summary`는 "어떤 필드가 바뀌었는지"만 기록할 뿐, 실제 조문 텍스트의 신구대조는 제공하지 않는다.

이 설계는 다음을 다룬다:
1. law/adrule의 "지금 적용되는 버전"을 매일 재계산하는 daily flow.
2. 그 재계산 과정에서 "가장 최근 시행일자를 갖게 된 title"에 대해 조문별(article 단위) 신구대조를 생성.
3. 위 둘을 안정적으로 수행하기 위해 필요한 그룹핑 키(law_id/adrule_id) 정규화와 관련 크롤러/backfill 변경.

새 URL이 추가되는 방식(신규 개정판 크롤링)은 이 설계의 범위가 아니며 기존 그대로 유지한다.

## 프로덕션에서 확인된 기존 버그

MongoDB(`original_db`)에서 실측한 결과:

- `law` 컬렉션: 동일 title에 `metadata.is_active: True`인 `doc_id`가 2개 이상인 title이 15건 존재 (예: 고용보험법 4개, 근로기준법 3개).
- `adrule` 컬렉션: adrule은 애초에 버전 관리/비활성화 로직이 없어 재크롤링 시 기존 `doc_id`의 문서를 비활성화하지 않고 그대로 upsert만 하고 있음. 동일 title에 대해 이미 2건의 중복 활성 사례 확인.
- 근로기준법 상세 사례:

  | doc_id | law_id | is_upcoming | effective | 상태 |
  |---|---|---|---|---|
  | 265959 | `001872` (안정 코드) | False | 2025-10-23 | 현행 |
  | 285279 | `근로기준법` (title 텍스트) | True | 2027-01-01 | 시행예정 ① |
  | 286771 | `근로기준법` (title 텍스트) | True | 2027-06-10 | 시행예정 ② |

  현재 3개가 동시에 활성인 것 자체는 (현행 1 + 개정이 순차 예정된 시행예정 2) 정상일 수 있다. 하지만 `law_id` 값이 레코드마다 다른 형식(숫자 코드 vs title 텍스트)으로 저장되어 있어, 2027-01-01이 도래해 285279가 현행으로 승격되어야 할 때 기존 승격 로직(`scraper.py`의 `update_many` 블록, `metadata.law_id` 매칭)이 값 불일치로 실패한다. 즉 지금은 잠복 상태이고, 실제로는 그 시점에 "현행이 2개"라는 눈에 보이는 오류로 나타난다.
- `law_id` 불일치의 원인: `law/logic/list_scraper.py`가 시행예정 항목까지 수집하기 위해 Playwright 기반 페이지 파싱을 쓰는데, 이 경로에서는 안정적인 법령ID를 직접 얻을 수 없어 title 텍스트를 `law_id`로 대신 저장하고 있다. 반면 일부 레거시 레코드는 숫자 코드 형태의 `law_id`를 갖고 있어 두 형식이 혼재한다.

## 데이터 모델 변경

### 그룹핑 키: `law_id` / `adrule_id`

`doc_id`는 revision(URL)마다 바뀌므로 "같은 법령/행정규칙"을 추적하는 키로 쓸 수 없다. law.go.kr이 제공하는 안정 ID를 그룹핑 키로 쓴다.

- `law`: `metadata.law_id`를 항상 안정적인 법령ID(예: `001872`)로 통일. title 텍스트 값은 전부 정규화 대상.
- `adrule`: `metadata.adrule_id`를 신규 추가, 행정규칙ID를 저장. law의 `law_id`와 필드명 패턴을 그대로 따름(두 컬렉션은 완전히 분리된 도메인이므로 통합 필드명을 쓸 이유가 없음).

**ID 조회 방법**: law.go.kr의 상세조회 API(`lawService.do`)는 `doc_id`(lsiSeq/admRulSeq)로 직접 안정 ID를 반환한다. title 검색/매칭이 전혀 필요 없다.

```
법령:   GET lawService.do?target=law&MST=<doc_id>&type=JSON
        → 기본정보.법령ID

행정규칙: GET lawService.do?target=admrul&ID=<doc_id>&type=JSON
        → 행정규칙기본정보.행정규칙ID
```

(위 두 엔드포인트는 2026-07-17에 실제 호출로 검증함: MST=265959 → 법령ID=001872, ID=2100000281780 → 행정규칙ID=51459.)

### `is_active` / `is_upcoming`

기존 논의에서 확정된 대로 두 필드를 계속 분리 유지한다. 의미가 다른 두 축이기 때문이다.

- `is_active`: 이 revision(doc_id)이 현재 우리가 "유지 중인" 버전인가 (과거에 superseded된 버전은 False로 내려감).
- `is_upcoming`: 이 revision의 시행일자가 아직 도래하지 않았는가 (`effective > 오늘`).

`is_active=True`이면서 `is_upcoming=True`인 문서는 유효하다 — "아직 시행 전이지만 우리가 추적해야 하는 버전"이라는 뜻이며, 위 근로기준법 사례처럼 여러 개가 동시에 존재할 수 있다(순차적으로 예정된 여러 개정).

두 필드 모두 `effective > 오늘` 계산으로 직접 판정하며, adrule API의 `현행연혁구분` 필드는 의미가 모호하여(오늘 기준 시행 여부인지, 최신 개정 여부인지 확실치 않음) 신뢰 소스로 사용하지 않는다.

### `update_summary` — 조문별 신구대조

기존: `change_summary.fields_changed` (어떤 필드가 바뀌었는지 리스트) — 이번 설계에서 제거하지 않고, 별도로 `update_summary`가 조문 단위 신구대조를 제공하도록 신규 추가.

- 트리거: 일일 refresh flow가 어떤 그룹(law_id/adrule_id)의 "현행"이 바뀌는(old_current → new_current) 것을 감지할 때만 생성. 새로 발견된 시행예정 revision이 처음 크롤링되는 시점(아직 시행 전)에는 생성하지 않는다 — 승격되는 순간이 "가장 최근 시행일자를 가진 title"이 확정되는 시점이기 때문.
- 대상: content가 실제로 달라진 조문만 포함 (필드 변경/메타데이터만 바뀐 조문은 제외).
- 형식: 구문(old) 전문 + 신문(new) 전문을 병기.
- 매칭 키: `article_number` (예: `"1"`, `"5.1"` — 제5조의1 형식). 한국 입법 관행상 조 삭제 시 뒷조문 번호를 당기지 않고 "삭제"로 표시하며, 신설 조문은 "...의N" 서브번호를 붙이므로 article_number는 개정 전후로 안정적인 키다.
- 분류: old_current와 new_current 각각의 활성 조문을 `{article_number: content}` map으로 만들어 합집합을 순회.

  | 케이스 | 조건 | update_summary |
  |---|---|---|
  | 개정 | 양쪽에 존재, content 다름 | old 전문 + new 전문 |
  | 신설 | new에만 존재 | old=null, new 전문 |
  | 삭제 | old에만 존재, 또는 new의 content가 "삭제" 문구로 대체됨 | old 전문, new=null 또는 "삭제" |
  | 무변경 | 양쪽에 존재, content 동일 | 제외 |

- 구현 위치: `scripts/core/database/change_detector.py`에 신규 `ArticleDiffBuilder` — old/new 조문 리스트를 받아 위 표대로 분류된 리스트를 반환하는 순수 함수. 기존 `ChangeDetector.compare_documents`(크롤링 시점 단일 문서 비교)와는 별개 경로이며 서로 대체하지 않는다.

## 일일 Refresh Flow

새 Prefect flow: `refresh_current_status_flow` (`scripts/core/flows/refresh_current_status_flow.py`). 매일 1회 실행, 사이트 접근 없이 MongoDB 메타데이터만 읽고 쓴다.

law, adrule 컬렉션 각각에 대해 다음을 수행:

1. `metadata.is_active=True`인 모든 문서를 `(law_id 또는 adrule_id, doc_id)`로 그룹핑. doc_id당 대표 `effective`/`is_upcoming` 값 하나만 추출(같은 doc_id의 모든 조문은 동일 값을 가짐).
2. 그룹키(law_id/adrule_id)별로:
   a. `effective <= 오늘`인 후보 중 `effective`가 가장 최신인 doc_id를 **new_current**로 결정.
   b. 그 그룹에서 기존에 `is_upcoming=False`였던 doc_id를 **old_current**로 조회.
   c. **승격 발생** (`new_current != old_current`)인 경우:
      - old_current의 모든 조문을 `is_active=False`로 비활성화.
      - new_current를 `is_active=True, is_upcoming=False`로 확정.
      - `ArticleDiffBuilder`로 old_current ↔ new_current 조문 diff 계산, content가 실제로 다른 조문만 new_current 쪽 조문 문서의 `metadata.update_summary`에 기록.
   d. 승격이 없으면 상태만 확인하고 넘어감 (변경 없음).
   e. `effective > 오늘`인 시행예정 후보는 `is_active=True, is_upcoming=True` 유지. 단 동일 `effective`에 doc_id가 2개 이상이면(중복 크롤링) `created_at`이 가장 최신인 것만 남기고 나머지는 `is_active=False` 처리.
   f. `effective <= 오늘`이지만 new_current가 아닌 나머지(이미 superseded된 과거 버전)는 `is_active=False` 확정.

이 로직은 `law_id`/`adrule_id`가 정규화되어 있다는 전제 위에서만 정확히 동작한다 (Backfill 섹션 참조).

## 크롤러 변경

### law

- `list_scraper.py`: 기존 `is_upcoming` 계산 로직 유지. 변경 없음.
- `scraper.py`:
  - 새 doc_id가 저장될 때 `lawService.do?target=law&MST=<doc_id>` 호출 → `metadata.law_id`에 법령ID 저장.
  - 기존 Step-0 promotion 블록(`update_many`로 `metadata.law_id` 매칭해 비활성화하던 코드) **삭제**. 승격/비활성화 책임은 일일 refresh flow로 완전히 이관하여, 크롤링 경로와 상태 판단 경로를 분리한다. (새 URL 발견 시 그냥 크롤링해서 추가하면 된다는 원래 요구사항과도 일치.)

### adrule

- `list_scraper.py`: `fetch_urls()` 반환 항목에 `is_upcoming`(`effective > 오늘`) 계산 추가. (`행정규칙ID`는 여기서 추출하지 않고 scraper.py에서 상세 API로 조회 — DRF 검색 API 응답의 필드를 바로 쓰지 않는 이유는 상세 API 쪽이 이미 law와 동일한 패턴이라 코드 일관성이 좋기 때문.)
- `scraper.py`:
  - 새 doc_id가 저장될 때 `lawService.do?target=admrul&ID=<doc_id>` 호출 → `metadata.adrule_id` 저장.
  - 조문 저장 경로를 raw `collection.update_one(doc_id+article_number, upsert=True)`에서 `UnifiedDocumentRepository.upsert_with_change_detection()` 호출로 교체 — law와 동일한 버전 관리 경로를 타도록 통일. 이것이 adrule에 지금 버전 이력이 전혀 없는 문제(재크롤링해도 이전 버전이 비활성화되지 않고 덮어쓰기만 됨)의 근본 수정이다.
  - promotion 로직은 추가하지 않음 (law와 동일하게 일일 refresh flow에 위임).

## Backfill 스크립트 (1회성)

`scripts/core/database/backfill_stable_ids.py` (가칭):

1. law, adrule 각 컬렉션에서 distinct `doc_id` 전체 조회.
2. doc_id별로 해당 상세 API(`target=law&MST=` / `target=admrul&ID=`) 호출 → 안정 ID 획득.
3. `update_many({"doc_id": doc_id}, {"$set": {"metadata.law_id": stable_id}})` (adrule은 `metadata.adrule_id`)로 그 doc_id의 모든 조문 row 일괄 갱신.
4. law.go.kr API 호출 rate-limit 고려 — 배치 처리 + 딜레이. 폐지/비공개 등으로 상세 API가 실패하는 doc_id는 로그만 남기고 스킵(수동 확인 대상으로 표시).
5. `--dry-run` 옵션: 실제 update 없이 "어떤 doc_id → 어떤 stable_id로 바뀔지"만 로그 출력.
6. 정규화 완료 후 `refresh_current_status_flow`를 1회 수동 트리거 — 이미 프로덕션에 존재하는 중복 활성 상태(law 15개 title, adrule 2개 title 이상)를 정리.

운영 절차: 맥미니에서 `--dry-run` 먼저 실행 → 결과 검토 → 실제 실행 → refresh flow 트리거.

## Prefect 배포

- `scripts/core/flows/refresh_current_status_flow.py` 신규 flow 파일.
- `deploy.py`에 `deploy_refresh_flow(schedule)` 함수 추가, `--refresh` 플래그로 개별 배포 (`python3 deploy.py --refresh`). 스케줄은 기존 주간 크롤러용 `--schedule` 인자와 별개로 daily 기본값(`'10 0 * * *'`, 매일 00:10 KST)을 하드코딩.
- 기존 주간 스크래퍼 배포(`python3 deploy.py`, scraper별 순회)는 변경 없음.

## 테스트 계획

**단위 테스트**
- `ArticleDiffBuilder`: article_number 매칭 기반 개정/신설/삭제/무변경 4분류 정확성. 조의2 서브번호, "삭제" 문구 케이스 포함.
- 일일 refresh 승격 판단 로직: mock 문서 세트(다양한 law_id 그룹/effective 조합)로 new_current 선정, old_current 비활성화, 복수 시행예정 유지, 동일 effective 중복 제거 검증.
- law_id/adrule_id 조회 헬퍼: `lawService.do` 응답 mock 파싱, 404/필드 누락 처리 검증.

**통합 테스트**
- 근로기준법 실제 버그 상황(law_id 불일치, 3개 active)을 재현한 테스트 컬렉션에 backfill + refresh flow 실행 → 현행 1개 + 시행예정 2개로 정리되고, update_summary는 승격이 실제로 발생한 경우에만 채워지는지 확인.
- adrule의 `upsert_with_change_detection` 전환: 내용 변경 시 new_version 생성 + 기존 비활성화, 내용 동일 시 중복 없이 메타데이터만 갱신되는지 확인.

**Backfill 검증**
- `--dry-run`으로 근로기준법을 포함한 샘플에 대해 의도한 law_id 변경 내역이 올바른지 확인 후 실제 실행.

## 범위 밖

- 새 개정판(URL) 발견 시 크롤링/저장하는 로직 자체는 변경하지 않는다 (`filter_new_urls`, weekly crawl 흐름 등).
- law/adrule 외 나머지 5개 문서 타입(case, decision, interpretation, mediation_case, judgment)은 이번 설계의 대상이 아니다 — 이들은 title 동일 + 시행일자만 다른 revision 개념이 없다.
