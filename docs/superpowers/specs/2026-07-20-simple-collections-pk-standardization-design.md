# 8종 컬렉션 저장 표준화 (PK 통일 + 중복 방지) 설계

## 배경

roeum-crawler가 다루는 10개 문서 유형 중 law/adrule은 별도 스펙(`2026-07-17-law-adrule-version-tracking-design.md`)에서 버전관리(is_active/is_upcoming) + 조문별 신구대조를 다룬다. 나머지 8종(case, interpretation, judgment, constitutional_decc, legislation_expc, admin_decc, decision, mediation_case)은 버전관리가 필요 없지만, 중복을 걸러내는 기준(PK)이 컬렉션마다 일관되게 적용되어야 한다는 요구에서 이 설계가 출발했다.

## 프로덕션에서 확인된 현재 상태

- 8종 모두 `scripts/core/identifiers.py`의 공통 헬퍼(`article_chunk_id`/`section_chunk_id`/`document_chunk_id`)로 `chunk_id` 문자열을 만들어 문서에 저장한다 — 이름 생성 방식 자체는 이미 통일되어 있다.
- 그러나 실제 upsert에 쓰이는 필터(진짜 중복판별 기준)는 `chunk_id`가 아니라 컬렉션별 **자연 키(natural key)** 조합이다:
  - interpretation: `{doc_id, article_number}`
  - case, constitutional_decc, legislation_expc, admin_decc: `{doc_id, doc_type, chunk_seq}`
  - judgment: `{doc_id}`
- 저장 경로가 갈려 있다: decision, mediation_case는 `UnifiedDocumentRepository.upsert_with_change_detection()`(버전관리형)을 쓰고, 나머지 6종은 각 스크래퍼 파일 안에 복붙된 `collection.update_one(...)` 직접 호출을 쓴다.
- **DB 차원의 unique 인덱스가 전혀 없다.** `UnifiedDocumentRepository`가 만드는 `chunk_id` 인덱스(`idx_citation_chunk_hash`)는 unique=False이고, 나머지 6종은 인덱스 자체가 없다. 애플리케이션 로직(먼저 조회 후 upsert)에만 의존하기 때문에 레이스 컨디션이나 로직 변경 시 중복 삽입을 막지 못한다.
  - 실제로 law 컬렉션(별도 스펙 대상이지만 동일 구조 사용)에서 진짜 중복 277건, chunk_id 포맷이 두 시점 사이 바뀌면서 생긴 잔존 구버전 103건이 확인됨 — 8종에도 같은 리스크가 잠재해 있다.
  - 코드베이스에는 `doc_id`/`chunk_id`에 unique=True 인덱스를 거는 레거시 `DocumentRepository`/`ChunkRepository`(`scripts/core/database/repository.py`)가 이미 존재하지만, 현재 10종 스크래퍼 어디에서도 쓰이지 않는 죽은 코드다.
- `case.py`의 `save_case_to_mongodb`/`save_case_to_mongodb_async`(필터: `{doc_id}` 단독, `chunk_id` 없이 저장)는 실제 크롤링 흐름에서 호출되지 않는 죽은 코드로 확인됨.
- `adrule.py`는 `UnifiedDocumentRepository`를 import만 하고 실제로 호출하지 않는 미완성 상태(2026-02-08 커밋에서 추가된 채 방치). 이번 설계에서 decision/mediation_case를 다운그레이드하면서 같은 패턴(단순 upsert)으로 통일하므로, adrule의 이 미사용 import도 함께 제거한다.

## 결정 사항

- **decision, mediation_case는 나머지 6종과 동일하게 단순 upsert로 다운그레이드한다.** `upsert_with_change_detection()` 호출과 `metadata.is_active` 필드를 제거한다. (버전관리는 law/adrule 전용 개념으로 한정)
- **unique 인덱스는 `chunk_id`가 아니라 각 컬렉션이 실제로 쓰는 자연 키 조합에 건다.** `chunk_id`는 계속 `identifiers.py` 헬퍼로 계산되어 매 저장 시 `$set`으로 갱신되는 파생/표시용 필드로 유지한다 — 자연 키를 PK로 쓰면 향후 `chunk_id` 생성 포맷이 바뀌어도(law의 103건 사례처럼) 자동으로 갱신될 뿐 새 중복이 생기지 않는다.
- **8종 전체가 하나의 공통 저장 헬퍼를 통해 upsert하도록 통합한다.** 로직이 스크래퍼 파일마다 복붙되어 있으면 한 곳만 고쳐질 때 다시 어긋난다(adrule의 미완성 import가 실제 사례).

## 아키텍처

새 모듈 `scripts/core/database/simple_document_store.py`에 공통 헬퍼를 추가한다:

```python
def upsert_chunk(collection, filter_fields: dict, doc: dict) -> str:
    """filter_fields로 upsert. 반환값: "inserted" | "updated" | "unchanged" """
```

- `filter_fields`는 호출부(각 스크래퍼)가 컬렉션 구조에 맞게 넘긴다 (예: interpretation은 `{"doc_id": doc_id, "article_number": article_number}`).
- 헬퍼 초기화 시(또는 최초 호출 시) 해당 컬렉션에 `filter_fields`의 키 조합으로 unique 인덱스를 생성한다 (`create_index([...], unique=True)`, 이미 존재하면 무시).
- `doc`는 지금처럼 `chunk_id`(identifiers.py로 계산된 값) 포함 전체 필드를 담아 `$set`, `created_at`은 `$setOnInsert`로 최초 삽입 시에만 설정.
- `DuplicateKeyError` 발생 시(동시 크롤링 등으로 unique 인덱스 생성 후에도 이론상 가능): 같은 filter_fields로 재조회 후 update로 1회 재시도. 재시도도 실패하면 해당 청크만 실패 처리하고 나머지 청크는 계속 진행 (기존 스크래퍼들의 "실패해도 다음 청크 계속" 패턴 유지).

### 적용 대상과 변경 내용

| 컬렉션 | 기존 저장 방식 | 변경 후 |
|---|---|---|
| case | `update_one` 직접 호출 (2가지 필터 혼재, 그 중 하나는 죽은 코드) | `upsert_chunk` 사용, 죽은 코드(`save_case_to_mongodb*`) 삭제 |
| interpretation | `update_one` 직접 호출 | `upsert_chunk` 사용 |
| judgment | `update_one` 직접 호출 | `upsert_chunk` 사용 |
| constitutional_decc | `update_one` 직접 호출 | `upsert_chunk` 사용 |
| legislation_expc | `update_one` 직접 호출 | `upsert_chunk` 사용 |
| admin_decc | `update_one` 직접 호출 | `upsert_chunk` 사용 |
| decision | `UnifiedDocumentRepository.upsert_with_change_detection()` | `upsert_chunk` 사용, `is_active` 필드 제거 |
| mediation_case | `UnifiedDocumentRepository.upsert_with_change_detection()` | `upsert_chunk` 사용, `is_active` 필드 제거 |

adrule의 미사용 `UnifiedDocumentRepository` import도 이 작업에서 함께 제거한다 (adrule 자체의 버전관리 구현은 별도 스펙 대상).

## Backfill 정리 스크립트 (1회성)

`scripts/migrations/dedupe_and_index_simple_collections.py`:

1. 8종 컬렉션을 순회하며 각자의 자연 키 조합으로 `$group` — 그룹 크기 2 이상인 것만 추출.
2. 그룹별로 `metadata.updated_at` 기준 최신 1건만 남기고 나머지 `_id`를 삭제 대상으로 표시.
3. 기본값은 `--dry-run` (삭제 대상 건수/샘플만 출력). `--execute` 플래그로 실제 삭제 수행.
4. 정리 완료 후 컬렉션별 자연 키 조합에 unique 인덱스 생성.

실행 순서: 먼저 `--dry-run`으로 프로덕션 DB 대상 삭제 대상 건수를 확인하고(law의 103+277건과 유사한 규모인지 검토), 이상 없으면 `--execute` → 인덱스 생성.

## 테스트 계획

- `upsert_chunk` 헬퍼 단위 테스트: 신규삽입 / 기존 문서 업데이트 / 내용 변경없음 / `DuplicateKeyError` 재시도 4가지 케이스.
- 8종 중 필터 패턴이 다른 2종(interpretation — article 기반, case — doc_type+chunk_seq 기반)을 실제로 헬퍼에 연동한 뒤 소량(`--pages 1`) 크롤로 회귀 테스트.
- backfill 스크립트 `--dry-run` 결과를 프로덕션 DB에서 먼저 검증한 후 `--execute`.

## 범위 밖

- law, adrule의 버전관리/신구대조는 기존 승인된 스펙(`2026-07-17-law-adrule-version-tracking-design.md`)에서 별도로 진행한다.
- `chunk_id` 자체를 실제 PK(unique 인덱스 대상)로 승격하는 것은 하지 않는다 — 자연 키가 더 안정적이라고 판단.
- 신규 URL 발견/크롤링 로직 변경은 다루지 않는다.
