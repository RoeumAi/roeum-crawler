# roeum-crawler MongoDB 저장 형식 및 DB 현황 분석 (2026-07-20)

- 코드 기준: `/Users/woong/Documents/Roeum/roeum-crawler` (로컬 체크아웃)
- 데이터 기준: 맥미니(운영) `/Users/loum/loum/roeum-crawler`가 연결하는 MongoDB Atlas `original_db` (2026-07-20 라이브 쿼리, 읽기 전용)
  - 로컬 `.env`의 MongoDB 자격증명은 인증 실패 상태라 로컬에서는 직접 조회 불가 — 맥미니를 통해 조회함
- 전 항목 라이브 쿼리로 검증. 중복 카운트는 최초 `(doc_id, article_number)` 기준으로 스캔했다가 4개 타입(case/constitutional_decc/legislation_expc/admin_decc)에서 오탐(false positive)을 발견하여 `chunk_id` 기준으로 재검증함 (사유는 해당 섹션 참고).

## 요약 표

| 타입 | 컬렉션 | 총 문서수 | distinct doc_id | 평균 article/chunk 수 | 저장 방식 | is_active | is_upcoming | 알려진 이슈 |
|---|---|---:|---:|---:|---|---|---|---|
| 법령 | `law` | 9,632 | 176 | 54.7 | `UnifiedDocumentRepository.upsert_with_change_detection` (버전관리) | 있음 (True 9,252 / False 380) | 있음 (True 2,174 / False 6,202 / 누락 1,256) | law_id 포맷 불일치(숫자코드 vs title텍스트, 설계문서 기존 확인), chunk_id 중복 277건 잔존 |
| 행정규칙 | `adrule` | 6,227 | 438 | 14.2 | 단순 `update_one` upsert (버전관리 없음) | 있음 (전부 True) | **없음** (필드 자체 미존재) | is_active가 한 번도 False가 된 적 없음 — 사실상 비활성화 로직 없음. is_upcoming 필드 부재 |
| 판례 | `case` | 71,947 | 18,103 | 3.97 | 단순 `update_one` upsert (버전관리 없음) | 있음 (True 71,945 / 기타 2) | 없음 | source_url 누락 1,415건(≈2%) |
| 심의결정례 | `decision` | 542 | 520 | 1.04 | `upsert_with_change_detection` (버전관리) | 있음 (전부 True) | 없음 | source_url 누락 31건(≈5.7%), 실사용상 비활성화 발생 이력 없음 |
| 해석례 | `interpretation` | 15,792 | 7,844 | 2.01 | 단순 `update_one` upsert | 있음 (전부 True) | 없음 | source_url 누락 483건(≈3%) |
| 조정사건례 | `mediation_case` | 78 | 78 | 1.0 | `upsert_with_change_detection` (버전관리) | 있음 (전부 True) | 없음 | 없음 (가장 깨끗함, 규모도 작음) |
| 주요판정사례 | `judgment` | 407 | 407 | 1.0 | 단순 `update_one` upsert | 있음 (전부 True) | 없음 | 없음 |
| 헌재결정례 | `constitutional_decc` | 1,162 | 201 | 5.78 | 단순 `update_one` upsert | 있음 (전부 True) | 없음 | 없음 (초기 오탐 있었으나 재검증 결과 실제 중복 0건) |
| 법제처해석례 | `legislation_expc` | 708 | 177 | 4.0 | 단순 `update_one` upsert | 있음 (전부 True) | 없음 | 없음 (재검증 결과 실제 중복 0건) |
| 행정심판재결례 | `admin_decc` | 17,334 | 4,281 | 4.05 | 단순 `update_one` upsert | 있음 (전부 True) | 없음 | 없음 (재검증 결과 실제 중복 0건) |

**핵심 관찰**: 10개 타입 중 실제로 "버전 관리"(변경 감지 → 새 버전 생성 → 구버전 `is_active=false`)를 쓰는 건 `law`, `decision`, `mediation_case` 3개뿐이고, 나머지 7개는 단순 upsert라 `is_active`가 사실상 항상 True인 장식용 필드다. 그리고 버전관리 3종 중에서도 실제로 비활성화(False)가 발생한 이력이 있는 건 `law`뿐이다(decision/mediation_case는 100% True — 첫 저장 이후 콘텐츠 변경이 감지된 적이 없다는 뜻이지, 로직이 고장났다는 뜻은 아님).

---

## 1. 법령 (law)

**저장 로직**: `scripts/law/logic/scraper.py:248 save_to_mongodb()` → 조(article) 단위로 쪼갠 뒤 `scripts/core/database/unified_repository.py`의 `UnifiedDocumentRepository.upsert_with_change_detection()`(각 조마다 호출, scraper.py:351)로 저장. 신규 doc_id는 `is_active=true`로 insert, 동일 doc_id 재크롤 시 콘텐츠 변경 여부에 따라 새 버전 생성 또는 메타데이터만 갱신. `doc_id`가 바뀌는 신규 리비전이 들어오면(법령개정으로 새 URL 발급) 별도 문서로 추가 insert되며, **같은 law_id 그룹 내 구버전을 비활성화하는 로직은 별도로 존재하나 law_id 포맷 불일치로 인해 실제로는 잘 안 걸린다** (`docs/superpowers/specs/2026-07-17-law-adrule-version-tracking-design.md`에서 이미 상세 분석·설계 완료).

**필드 스키마 (샘플 기반)**:
```
_id, doc_id, article_number, article_number_numeric, article_title,
title, sub_title, content, content_hash, embedding,
metadata: {
  source_url, source_type, effective, created_at, updated_at, last_check_at,
  is_active, is_upcoming, law_id, dept_code, dept_name,
  article_index, total_articles,
  current_version, previous_version_id, version, change_summary{fields_changed[], content_hash_changed},
  embedding_updated_at, is_searchable
}
```

**DB 현황**:
- 총 9,632 rows / distinct doc_id 176개 (doc_id당 평균 54.7개 조문)
- `is_active`: True 9,252 / False 380 — 버전관리가 실제로 작동해서 구버전을 비활성화시킨 사례가 존재함 (10개 타입 중 유일)
- `is_upcoming`: True 2,174 / False 6,202 / **필드 자체 누락 1,256건** — 필드가 나중에 추가되면서 그 이전 저장분에는 안 채워진 것으로 보임
- `chunk_id` 기준 진짜 중복: **277 groups** (예: `law:287805:article:28`가 동일 _id 없이 2 rows 존재) — `crawler_cleanup_plan` 메모에 있던 "law 중복" 버그의 잔존분. 코드는 이미 수정됐고 DB 정리만 미완료 상태였는데, 이 규모(277건)면 대부분 정리는 됐고 소량만 남은 것으로 보임
- (doc_id, article_number) 기준 dup 379 groups — chunk_id 기준(277)보다 큰 이유는 서로 다른 리비전(doc_id 자체가 다름)인데 article_number가 같은 경우까지 잡혀서(정상 케이스) 약간 부풀려짐. **실제 이슈 수치는 chunk_id 기준 277건**
- 최신 `updated_at`: 2026-07-13 / 최신 `last_check_at`: 2026-07-09 → 최근까지 갱신되고 있음

---

## 2. 행정규칙 (adrule)

**저장 로직**: `scripts/adrule/logic/scraper.py:398 save_to_mongodb()` → `collection.update_one({doc_id, article_number}, upsert=True)` (scraper.py:503). `UnifiedDocumentRepository`를 쓰지 않아 버전관리/비활성화 로직이 전혀 없음 — law과 동일하게 URL이 리비전마다 바뀌는데도 구버전을 비활성화하는 코드 자체가 없다.

**필드 스키마**:
```
_id, doc_id, article_number, article_title, content, doc_type,
title, sub_title, content_hash, embedding,
metadata: {
  source_url, source_type, effective, created_at, updated_at,
  is_active, article_index, total_articles, dept_code, dept_name
}
```
(law과 달리 `law_id`/`adrule_id`, `is_upcoming`, `current_version`, `change_summary` 등 버전관리 필드가 아예 없음 — 설계문서에서 이미 확인된 부분)

**DB 현황**:
- 총 6,227 rows / distinct doc_id 438개 (doc_id당 평균 14.2개 조문)
- `is_active`: **전부 True (False 0건)** — 코드에 비활성화 로직이 없으니 당연한 결과. 리비전이 바뀔 때마다 구/신 doc_id가 모두 계속 active로 남는 구조
- (doc_id, article_number) 기준 dup 0건 — update_one이 제대로 upsert 역할을 하고 있어 같은 doc_id 내 중복은 없음
- 최신 `updated_at`: 2026-07-11

---

## 3. 판례 (case)

**저장 로직**: `scripts/case/logic/scraper.py`에 두 개의 저장 함수가 있음 — `save_case_to_mongodb()`(:235, 문서 단위 `update_one`, :255)와 `save_case_chunks_to_mongodb()`(:277, 판시사항/판결요지 등 섹션별 청크 upsert, :345). 둘 다 단순 upsert, 버전관리 없음.

**필드 스키마**:
```
_id, doc_id, chunk_seq, doc_type(판시사항 등 섹션명), content, title, subtitle, sub_title,
article_number, article_title, chunk_id("case:{doc_id}:{doc_type}:{chunk_seq}"),
metadata: {
  chapter, chunk_index, created_at, updated_at, effective, is_active,
  source_type, source_url, token_count, total_chunks, total_sub_chunks, dept_name
}
```
(`subtitle`과 `sub_title` 두 필드가 동일 값으로 중복 저장됨 — 스키마 정리 여지가 있으나 기능상 문제는 아님)

**DB 현황**:
- 총 71,947 rows / distinct doc_id 18,103개 (doc_id당 평균 3.97 청크 — 판시사항/판결요지/이유 등 섹션 분할)
- `is_active`: True 71,945 / 기타(false 혹은 null) 2건 — 무시 가능한 수준
- **초기 재검증 필요했던 항목**: `(doc_id, article_number)` 기준으로는 dup 17,520 groups(distinct doc_id의 96%)가 나와서 심각한 중복처럼 보였으나, 이는 `article_number`가 doc_id 내에서 doc_type(섹션)별로 1부터 다시 시작하기 때문에 생기는 **오탐**이었음. 진짜 유니크 키인 `chunk_id` 기준으로 재검증하니 **중복 0건**. `crawler_cleanup_plan` 메모의 "case 중복 28,705건" 기록과는 별개 시점 수치로 보이며, 현재는 깨끗한 상태
- source_url 누락: 1,415 / 71,947 (≈2%)
- 최신 `created_at`/`updated_at`: 2026-07-20(오늘) — 활발히 갱신 중

---

## 4. 심의결정례 (decision)

**저장 로직**: `scripts/decision/logic/scraper.py:40 save_to_mongodb()` → `upsert_with_change_detection()` 사용(:54). law과 동일한 버전관리 경로를 씀.

**필드 스키마**:
```
_id, doc_id, doc_type, title, sub_title, content, article_number("1" 고정), chunk_id,
metadata: {
  source_url, source_type, effective, created_at, updated_at, last_check_at,
  is_active, dept_code, dept_name, current_version, previous_version_id,
  version, change_summary{fields_changed[], content_hash_changed}
}
```

**DB 현황**:
- 총 542 rows / distinct doc_id 520개 (doc_id당 평균 1.04 — 거의 분할 없이 문서 하나가 통째로 저장됨)
- `is_active`: 전부 True — 버전관리 코드는 있지만 실제로 콘텐츠 변경이 감지되어 비활성화된 사례는 아직 없음
- 중복 0건
- source_url 누락 31 / 542 (≈5.7%, 초기 수집분으로 추정)
- 최신 `updated_at`: 2026-07-09 / `last_check_at`: 2026-07-02

---

## 5. 해석례 (interpretation)

**저장 로직**: `scripts/interpretation/logic/scraper.py:130 save_to_mongodb()` → `collection.update_one()` 단순 upsert(:202). 버전관리 없음.

**필드 스키마**:
```
_id, doc_id, article_number, article_title, content, doc_type("법령해석"), title, sub_title, chunk_id,
metadata: {
  source_url, source_type, effective, created_at, updated_at, is_active,
  article_index, total_articles, dept_name, cpt_ofi_code, cpt_ofi_name, ofi_cls_cd
}
```

**DB 현황**:
- 총 15,792 rows / distinct doc_id 7,844개 (doc_id당 평균 2.01 조항)
- `is_active`: 전부 True
- 중복 0건 (`crawler_cleanup_plan`의 "interpretation 중복 2,880건" 기록과 달리 현재는 깨끗함 — 코드 수정 + 이전 정리 작업이 반영된 것으로 보임)
- source_url 누락 483 / 15,792 (≈3%)
- 최신 `created_at`/`updated_at`: 2026-07-09

---

## 6. 조정사건례 (mediation_case)

**저장 로직**: `scripts/mediation_case/logic/scraper.py:93 save_mediation_to_mongodb()` → `upsert_with_change_detection()` 사용(:102). 버전관리 경로.

**필드 스키마**:
```
_id, doc_id, content, doc_type("조정사건례"), title, sub_title, content_hash, embedding,
metadata: {
  source_url, source_type, created_at, updated_at, effective, is_active,
  embedding_updated_at, is_searchable, last_check_at, version, dept_name
}
```

**DB 현황**:
- 총 78 rows / distinct doc_id 78개 (1:1, 분할 없음) — 10개 타입 중 규모가 가장 작고 가장 깨끗함
- `is_active`: 전부 True, 중복 0건
- 최신 `updated_at`: 2026-02-09 / `last_check_at`: 2026-04-21 — 다른 타입 대비 최근 갱신 이력이 오래됨(자연스러운 것일 수 있음: 원천 데이터 자체가 신규 발생이 적은 유형)

---

## 7. 주요판정사례 (judgment)

**저장 로직**: `scripts/judgment/logic/scraper.py:84 save_judgment_to_mongodb()` → `collection.update_one()` 단순 upsert(:91). 버전관리 없음.

**필드 스키마**:
```
_id, doc_id, content, doc_type("주요판정사례"), title, sub_title, content_hash, embedding,
metadata: {
  source_url, source_type, created_at, updated_at, effective, is_active,
  is_searchable, dept_name
}
```

**DB 현황**:
- 총 407 rows / distinct doc_id 407개 (1:1)
- `is_active`: 전부 True, 중복 0건
- 최신 `updated_at`: 2026-06-29

---

## 8. 헌재결정례 (constitutional_decc)

**저장 로직**: `scripts/constitutional_decc/logic/scraper.py:126 save_to_mongodb()` → 청크별 `collection.update_one()` 단순 upsert(:193). 버전관리 없음.

**필드 스키마**: case와 동일한 청크 구조
```
_id, doc_id, doc_type(판시사항 등), chunk_seq, content, title, subtitle, sub_title,
article_number, article_title, chunk_id("constitutional_decc:{doc_id}:{doc_type}:{chunk_seq}"),
metadata: {
  chapter, chunk_index, created_at, updated_at, is_active, is_metadata_only,
  source_type, source_url, token_count, total_chunks, total_sub_chunks, dept_name
}
```
(다른 청크형 타입엔 없는 `is_metadata_only` 플래그가 일부 문서에 존재 — 본문 구조가 없어 메타데이터만 저장된 케이스로 추정, 상세 원인은 미조사)

**DB 현황**:
- 총 1,162 rows / distinct doc_id 201개 (doc_id당 평균 5.78 청크)
- `is_active`: 전부 True
- `(doc_id, article_number)` 기준 초기 스캔에서 dup 164 groups로 나왔으나, `chunk_id` 기준 재검증 결과 **실제 중복 0건** (case와 동일한 오탐 패턴 — article_number가 섹션별로 재시작되는 구조 때문)
- 최신 `created_at`/`updated_at`: 2026-07-05

---

## 9. 법제처해석례 (legislation_expc)

**저장 로직**: `scripts/legislation_expc/logic/scraper.py:118 save_to_mongodb()` → 청크별 `collection.update_one()`(:185). 버전관리 없음.

**필드 스키마**: constitutional_decc/case와 동일한 청크 구조 (`chunk_id`: `legislation_expc:{doc_id}:{doc_type}:{chunk_seq}`)

**DB 현황**:
- 총 708 rows / distinct doc_id 177개 (doc_id당 평균 4.0 청크)
- `is_active`: 전부 True
- `(doc_id, article_number)` 기준 dup 177 groups(=doc_id 100%)로 나왔으나 `chunk_id` 기준 재검증 결과 **실제 중복 0건** (동일 오탐 패턴)
- 최신 `created_at`/`updated_at`: 2026-07-17 — 이 세션 앞부분에서 확인했던 "1건 업데이트" 신규 문서가 이 최신 시각과 일치

---

## 10. 행정심판재결례 (admin_decc)

**저장 로직**: `scripts/admin_decc/logic/scraper.py:122 save_to_mongodb()` → 청크별 `collection.update_one()`(:189). 버전관리 없음.
(참고: 맥미니 운영 체크아웃에는 이 파일이 BeautifulSoup 방식에서 DRF XML API 방식으로 마이그레이션 중인 미커밋 변경사항이 있음 — 이번 분석은 현재 DB에 이미 저장된 데이터 기준이라 영향 없음)

**필드 스키마**: 동일 청크 구조 (`chunk_id`: `admin_decc:{doc_id}:{doc_type}:{chunk_seq}`)

**DB 현황**:
- 총 17,334 rows / distinct doc_id 4,281개 (doc_id당 평균 4.05 청크) — 10개 타입 중 규모 2위
- `is_active`: 전부 True
- `(doc_id, article_number)` 기준 dup 4,279 groups(=doc_id 거의 100%)로 나왔으나 `chunk_id` 기준 재검증 결과 **실제 중복 0건** (동일 오탐 패턴)
- 최신 `created_at`/`updated_at`: 2026-07-13

---

## 종합 관찰

1. **버전관리 3종 vs 단순 upsert 7종**: `law`/`decision`/`mediation_case`만 `UnifiedDocumentRepository.upsert_with_change_detection()`을 쓰고, 나머지 7종(`adrule`/`case`/`interpretation`/`judgment`/`constitutional_decc`/`legislation_expc`/`admin_decc`)은 단순 `update_one` upsert라 `is_active`가 항상 True로 고정된 사실상 미사용 필드다. `adrule`이 여기 포함된다는 점은 기존 설계문서(`2026-07-17-law-adrule-version-tracking-design.md`)에서 이미 지적된 내용과 일치.
2. **is_upcoming은 law 전용**: 10종 중 `law`만 `is_upcoming` 필드를 가지고 있고 그마저 과거 저장분 1,256건은 필드 자체가 없다. `adrule`은 아예 없음 — 설계문서의 백필 계획 범위와 일치.
3. **law의 실제 잔존 중복 277건**: `crawler_cleanup_plan` 메모에 있던 law 중복 버그(당시 233,672건)는 코드 수정 후 대부분 정리된 것으로 보이나, `chunk_id` 기준으로 여전히 277 groups가 남아있음 — DB 정리 작업이 완전히 끝나지 않았을 가능성.
4. **초기 오탐 정정**: `case`/`constitutional_decc`/`legislation_expc`/`admin_decc` 4종은 `(doc_id, article_number)` 기준 그룹핑으로는 마치 대량 중복(해당 doc_id의 거의 100%)이 있는 것처럼 보였지만, 이 4종은 판시사항/재결요지/질의요지 등 **섹션마다 article_number/chunk_seq가 1부터 재시작**하는 구조라서 생긴 착시였다. 진짜 유니크 키인 `chunk_id`로 재검증한 결과 4종 전부 실제 중복 0건 — 실제 이슈 아님.
5. **source_url 누락**은 `case`(2%), `interpretation`(3%), `decision`(5.7%)에서 소량 존재 — 초기 수집분에서 필드가 채워지지 않은 것으로 추정, 별도 조사는 하지 않음.
6. **최신 갱신 시각**은 대부분 2026-07-05~07-20 범위로 최근까지 정상 갱신 중. `mediation_case`만 상대적으로 오래됨(원천 데이터 자체의 발생 빈도가 낮은 유형으로 추정, 문제로 단정하지 않음).
