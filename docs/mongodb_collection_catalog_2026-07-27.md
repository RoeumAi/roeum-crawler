# Roeum Crawler MongoDB 컬렉션 및 원천 페이지 명세

작성일: 2026-07-27  
대상 DB: MongoDB Atlas `original_db`  
운영 코드: 맥미니 `/Users/loum/loum/roeum-crawler`

## 1. 조사 기준

- 컬렉션명과 한글명은 `scripts/core/config.py`의 현재 `SCRAPERS` 설정을 기준으로 했다.
- 원천 URL, 요청 방식, 필터 조건은 각 `scripts/<collection>/logic/list_scraper.py`와 `crawl.py`를 확인했다.
- 건수와 실제 필드 구성은 2026-07-27 운영 MongoDB를 읽기 전용으로 조회했다.
- MongoDB의 “총 레코드 수”는 논리 문서 수와 다르다. 법령·판례처럼 한 문서를 여러 조문/섹션으로 나누는 컬렉션에서는 하나의 `doc_id`가 여러 MongoDB 레코드를 가진다.

## 2. 전체 컬렉션 요약

| 영문 컬렉션명 | 한글명 | MongoDB 레코드 | 논리 문서 (`distinct doc_id`) | 문서당 평균 청크 | 저장 단위 | 원천 기관 |
|---|---|---:|---:|---:|---|---|
| `law` | 법령 | 10,569 | 181 | 58.39 | 조문 | 국가법령정보센터 |
| `adrule` | 행정규칙 | 6,235 | 442 | 14.11 | 조문/섹션 | 국가법령정보센터 DRF |
| `case` | 판례 | 71,954 | 18,104 | 3.97 | 판시사항·이유 등 섹션 | 국가법령정보센터 |
| `decision` | 심의결정례 | 542 | 520 | 1.04 | 문서 | 중앙노동위원회 |
| `interpretation` | 해석례 | 15,792 | 7,844 | 2.01 | 질의·회답 등 조항 | 국가법령정보센터 |
| `mediation_case` | 조정사건례 | 78 | 78 | 1.00 | 문서 | 중앙노동위원회 |
| `judgment` | 주요판정사례 | 407 | 407 | 1.00 | 문서 | 중앙노동위원회 |
| `constitutional_decc` | 헌재결정례 | 1,162 | 201 | 5.78 | 주문·이유 등 섹션 | 국가법령정보센터 |
| `legislation_expc` | 법제처해석례 | 712 | 178 | 4.00 | 질의요지·회답·이유 등 섹션 | 국가법령정보센터 |
| `admin_decc` | 행정심판재결례 | 17,334 | 4,281 | 4.05 | 주문·이유 등 섹션 | 국가법령정보센터 DRF |

> 2026-07-27 장애 복구 후 `case`의 판례 `622053` 7개 청크와 `legislation_expc`의 해석례 `343515` 4개 청크가 포함된 수치다.

## 3. 공통 MongoDB 구조

대부분 컬렉션은 다음 공통 구조를 사용한다.

```javascript
{
  _id: ObjectId,
  doc_id: String,                 // 원천 사이트의 논리 문서 ID
  doc_type: String,               // 자료 유형 또는 섹션명
  chunk_id: String,               // RAG 인용·검색용 안정 청크 ID
  title: String,
  sub_title: String,
  content: String,
  content_hash: String,           // SHA-256
  source_version_id: String,      // collection + chunk_id + content_hash
  embedding: Array<Number>,       // 임베딩 생성 완료 문서에 존재
  embedding_hash: String,
  metadata: {
    source_url: String,
    source_type: "web",
    created_at: String,
    updated_at: String,
    is_active: Boolean,
    dept_name: String,
    embedding_updated_at: String,
    is_searchable: Boolean
  }
}
```

청크 ID 규칙은 세 종류다.

```text
조문형: collection:{doc_id}:article:{article_number}
섹션형: collection:{doc_id}:{doc_type_slug}:{chunk_seq}
문서형: collection:{doc_id}
```

현재 인덱스의 공통 축은 `doc_id`, `chunk_id`, `(doc_id, article_number)`,
`(doc_id, chunk_seq)`, `(doc_type, doc_id)`, `metadata.source_url`,
`metadata.created_at`, `metadata.last_check_at`이다. 컬렉션마다 일부만 존재하며,
운영 DB에서 확인한 `chunk_id` 인덱스는 현재 unique 인덱스가 아니다.

## 4. 컬렉션별 상세

### 4.1 `law` — 법령

**수집 대상**

- 국가법령정보센터 법령 목록:
  `https://www.law.go.kr/lsAstSc.do?menuId=391&subMenuId=397&tabMenuId=437`
- 고용노동부 `cptOfiCd=1492000`
- 개인정보보호위원회 `cptOfiCd=1790365`
- 상세 페이지:
  `https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={lsiSeq}&efYd={시행일}`

목록은 Playwright로 렌더링한다. 현행 법령과 시행예정 법령을 함께 읽고,
법령명·시행일·`lsiSeq`를 DOM의 `onclick` 값에서 추출한다.

**MongoDB 구성**

- 논리 ID: `doc_id = lsiSeq`
- 그룹 ID: `metadata.law_id = 법령명`
- 저장 단위: 조문
- 청크 ID: `law:{doc_id}:article:{article_number}`
- 주요 추가 필드:
  `article_number`, `article_number_numeric`, `article_title`
- 주요 metadata:
  `effective`, `is_upcoming`, `law_id`, `article_index`, `total_articles`,
  `version`, `current_version`, `previous_version_id`, `change_summary`
- 저장 방식: `UnifiedDocumentRepository.upsert_with_change_detection()`

### 4.2 `adrule` — 행정규칙

**수집 대상**

- DRF 검색 API:
  `https://www.law.go.kr/DRF/lawSearch.do`
- 요청 조건: `target=admrul`, `type=JSON`, 페이지당 100건
- 전체 행정규칙을 순회한 뒤 소관부처명이 다음 조건인 항목만 선택한다.
  - 이름에 `고용노동부` 포함
  - `노동부`, `중앙노동위원회`, `노동위원회`
- 상세 페이지:
  `https://www.law.go.kr/admRulInfoP.do?admRulSeq={행정규칙일련번호}`

**MongoDB 구성**

- 논리 ID: `doc_id = 행정규칙일련번호`
- 그룹 ID: `metadata.adrule_id`
- 저장 단위: 조문 또는 서문/섹션
- 청크 ID: `adrule:{doc_id}:article:{article_number}`
- 주요 추가 필드:
  `article_number`, `article_title`
- 주요 metadata:
  `adrule_id`, `effective`, `article_index`, `total_articles`,
  `dept_code`, `dept_name`
- 현재 저장 코드: `UnifiedDocumentRepository.upsert_with_change_detection()`

### 4.3 `case` — 판례

**수집 대상**

- 목록 화면:
  `https://www.law.go.kr/LSW/precAstSc.do?menuId=391&subMenuId=397&tabMenuId=443&cptOfiCd=1492000`
- 실제 목록 POST:
  `https://www.law.go.kr/LSW/precAstScListR.do`
- 상세 페이지:
  `https://www.law.go.kr/LSW/precInfoP.do?mode=0&precSeq={precSeq}`

`cptOfiCd=1492000` 조건으로 목록을 순회하며 `precSeq`를 추출한다.

**MongoDB 구성**

- 논리 ID: `doc_id = precSeq`
- 저장 단위: 판시사항, 판결요지, 참조조문, 참조판례, 전문, 주문,
  청구취지, 이유 등의 섹션
- 청크 ID: `case:{doc_id}:{doc_type}:{chunk_seq}`
- 주요 추가 필드:
  `chunk_seq`, `article_number`, `article_title`, `subtitle`
- `sub_title`과 호환 필드 `subtitle`에는 재판부·선고일·결과를 섞지 않고
  상세 페이지의 `#detcNo` 사건번호만 저장한다. 예: `2019헌바454`
- 주요 metadata:
  `chapter`, `effective`, `chunk_index`, `total_chunks`,
  `total_sub_chunks`, `token_count`
- 저장 방식:
  `(doc_id, doc_type, chunk_seq)` 조건의 `update_one(..., upsert=True)`
- 동일 콘텐츠 재확인 시 `content_hash`를 비교하고
  `metadata.last_check_at`만 갱신한다.

### 4.4 `decision` — 심의결정례

**수집 대상**

- 중앙노동위원회 화면:
  `https://nlrc.go.kr/nlrc/mainCase/judgment/index.do`
- 실제 목록 POST:
  `https://nlrc.go.kr/nlrc/mainCase/mainJudgment/list.do`
- 구분 코드: `jgmtDcsnSeCd=67`

심의결정례는 별도 상세 본문이 없으므로 목록의 제목과 등록일을 문서 내용으로
저장한다. 코드가 만드는 상세 URL은 문서 식별용이다.

**MongoDB 구성**

- 논리 ID: `doc_id = jgmtSn`
- 저장 단위: 문서
- 청크 ID: `decision:{doc_id}`
- `doc_type = "심의결정례"`, `article_number = "1"`
- 주요 metadata:
  `effective`, `version`, `current_version`, `previous_version_id`,
  `change_summary`, `last_check_at`
- 저장 방식: `UnifiedDocumentRepository.upsert_with_change_detection()`

### 4.5 `interpretation` — 해석례

**수집 대상**

- 화면:
  `https://www.law.go.kr/cgmExpcAstSc.do?menuId=391&subMenuId=397&tabMenuId=741`
- 실제 목록 POST:
  `https://www.law.go.kr/LSW/cgmExpcAstScListR.do`
- 부처 조건: `cptOfi=1492000`(고용노동부)
- 상세 페이지:
  `https://www.law.go.kr/LSW/cgmExpcInfoP.do?cgmExpcDatSeq={ID}&ofiClsCd={기관분류}`

**MongoDB 구성**

- 논리 ID: `doc_id = cgmExpcDatSeq`(목록의 `doc_seq`)
- 저장 단위: 해석문의 조항/섹션
- 청크 ID: `interpretation:{doc_id}:article:{article_number}`
- `doc_type = "법령해석"`
- 주요 추가 필드:
  `article_number`, `article_title`
- 주요 metadata:
  `effective`, `cpt_ofi_code`, `cpt_ofi_name`, `ofi_cls_cd`,
  `article_index`, `total_articles`
- 저장 방식:
  `(doc_id, article_number)` 조건의 `update_one(..., upsert=True)`

### 4.6 `mediation_case` — 조정사건례

**수집 대상**

- 중앙노동위원회 화면:
  `https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do`
- 실제 목록 POST:
  `https://nlrc.go.kr/nlrc/mainCase/mainJudgment/list.do`
- 구분 코드: `jgmtDcsnSeCd=66`
- 상세 페이지:
  `https://nlrc.go.kr/nlrc/mainCase/mediatioin/detail.do?jgmtSn={ID}&jgmtDcsnSeCd=66`

원천 사이트 경로의 `mediatioin` 철자는 실제 사이트가 사용하는 값이다.

**MongoDB 구성**

- 논리 ID: `doc_id = jgmtSn`
- 저장 단위: 문서
- 청크 ID: `mediation_case:{doc_id}`
- `doc_type = "조정사건례"`
- 상세 페이지의 공식 PDF/HWP/HWPX 첨부를 우선 추출한다. 텍스트 PDF와
  HWP/HWPX는 native parsing, 스캔 PDF는 `chat_generation /api/extract-text`
  OCR 결과를 본문에 추가한다.
- 신규 URL도 동일하게 지원 첨부파일을 확인한다. 첨부가 있는데 추출 API·다운로드가 일시
  실패하면 `metadata.pdf_retry_needed=true`로 저장하여 다음 일일 실행에서
  자동 재시도한다. PDF가 없는 게시물은 HTML 본문을 정상 결과로 유지한다.
- 주요 metadata:
  `effective`, `version`, `last_check_at`, `attachment_name`,
  `attachment_url`, `attachment_file_id`, `content_source`,
  `pdf_page_count`, `pdf_is_searchable`, `pdf_cost_usd`, `pdf_error`
- 저장 방식: `UnifiedDocumentRepository.upsert_with_change_detection()`

### 4.7 `judgment` — 주요판정사례

**수집 대상**

- 중앙노동위원회 화면:
  `https://nlrc.go.kr/nlrc/mainCase/judgment/index.do`
- 실제 목록 POST:
  `https://nlrc.go.kr/nlrc/mainCase/mainJudgment/list.do`
- 구분 코드: `jgmtDcsnSeCd=65`
- 상세 페이지:
  `https://nlrc.go.kr/nlrc/mainCase/judgment/detail.do?jgmtSn={ID}&jgmtDcsnSeCd=65`

**MongoDB 구성**

- 논리 ID: `doc_id = jgmtSn`
- 저장 단위: 문서
- 청크 ID: `judgment:{doc_id}`
- `doc_type = "주요판정사례"`
- 상세 페이지의 공식 PDF/HWP/HWPX 첨부를 내려받아
  `chat_generation /api/extract-text`로 native text/OCR 추출하고,
  HTML 판정요지와 첨부파일 전문을 함께 저장한다.
- 한 상세 페이지에 지원 첨부파일이 여러 개 있으면 첫 파일만 선택하지 않고
  모든 첨부의 전문을 파일명과 함께 결합하여 저장한다.
- 신규 URL도 동일하게 지원 첨부파일을 확인한다. 첨부가 있는데 추출 API·다운로드가 일시
  실패하면 `metadata.pdf_retry_needed=true`로 저장하여 다음 일일 실행에서
  자동 재시도한다. PDF가 없는 게시물은 HTML 본문을 정상 결과로 유지한다.
- 주요 metadata:
  `effective`, `source_url`, `is_active`, `is_searchable`,
  `attachment_name`, `attachment_url`, `attachment_file_id`,
  `content_source`, `pdf_page_count`, `pdf_is_searchable`,
  `pdf_cost_usd`, `pdf_error`
- 저장 방식: `doc_id` 조건의 `update_one(..., upsert=True)`

### 4.8 `constitutional_decc` — 헌재결정례

**수집 대상**

- 실제 목록 POST:
  `https://www.law.go.kr/LSW/detcAstScListR.do`
- 요청 조건: `cptOfi=1492000`
- 상세 페이지:
  국가법령정보센터가 목록에 제공하는
  `detcInfoP.do?...detcSeq={detcSeq}` 링크

**MongoDB 구성**

- 논리 ID: `doc_id = detcSeq`
- 저장 단위:
  결정요지, 심판대상조문, 주문, 이유, 참조조문, 참조판례, 별지 등
- 청크 ID:
  `constitutional_decc:{doc_id}:{doc_type}:{chunk_seq}`
- 주요 추가 필드:
  `chunk_seq`, `article_number`, `article_title`, `subtitle`
- 주요 metadata:
  `chapter`, `is_metadata_only`, `chunk_index`, `total_chunks`,
  `total_sub_chunks`, `token_count`
- 저장 방식:
  `(doc_id, doc_type, chunk_seq)` 조건의 `update_one(..., upsert=True)`

### 4.9 `legislation_expc` — 법제처해석례

**수집 대상**

- 실제 목록 POST:
  `https://www.law.go.kr/LSW/expcAstScListR.do`
- 요청 조건: `cptOfi=1492000`
- 상세 페이지:
  `https://www.law.go.kr/LSW/expcInfoP.do?mode=2&expcSeq={expcSeq}`

**MongoDB 구성**

- 논리 ID: `doc_id = expcSeq`
- 저장 단위:
  질의요지, 회답, 이유, 법제처 법령해석의 효력 안내
- 청크 ID:
  `legislation_expc:{doc_id}:{doc_type}:{chunk_seq}`
- 주요 추가 필드:
  `chunk_seq`, `article_number`, `article_title`, `subtitle`
- 주요 metadata:
  `chapter`, `chunk_index`, `total_chunks`, `total_sub_chunks`, `token_count`
- 저장 방식:
  `(doc_id, doc_type, chunk_seq)` 조건의 `update_one(..., upsert=True)`

### 4.10 `admin_decc` — 행정심판재결례

**수집 대상**

- DRF 검색 API:
  `https://www.law.go.kr/DRF/lawSearch.do`
- 요청 조건:
  `target=decc`, `type=XML`, 페이지당 100건
- 상세 페이지:
  `https://www.law.go.kr/LSW/deccInfoP.do?deccSeq={ID}&mode=3`

DRF의 전체 행정심판재결례를 순회한 뒤 사건명과 재결구분명에 노동 관련
키워드가 포함된 항목만 수집한다. 예: 근로, 임금, 해고, 고용보험, 산재,
노동조합, 퇴직금, 직장 내 괴롭힘 등.

**MongoDB 구성**

- 논리 ID: `doc_id = deccSeq`
- 저장 단위: 주문, 청구취지, 이유, 관계 법령 등 페이지 섹션
- 청크 ID:
  `admin_decc:{doc_id}:{doc_type}:{chunk_seq}`
- 주요 추가 필드:
  `chunk_seq`, `article_number`, `article_title`, `subtitle`
- 주요 metadata:
  `chapter`, `chunk_index`, `total_chunks`, `total_sub_chunks`, `token_count`
- 저장 방식:
  `(doc_id, doc_type, chunk_seq)` 조건의 `update_one(..., upsert=True)`

## 5. 저장·업데이트 방식 분류

### 변경 감지 저장소 사용

- `law`
- `adrule`
- `decision`
- `mediation_case`

이 유형은 `UnifiedDocumentRepository.upsert_with_change_detection()`를 사용해
신규 저장, 새 버전, 내용 변경 없음 상태를 구분한다.

### 단순 upsert 사용

- `case`
- `interpretation`
- `judgment`
- `constitutional_decc`
- `legislation_expc`
- `admin_decc`

이 유형은 논리 PK로 `update_one(..., upsert=True)`를 수행한다. `case`는 별도로
`content_hash`를 비교해 변경 없는 청크의 전체 내용을 다시 쓰지 않는다.

## 6. 운영상 확인 사항

1. **레코드 수와 실제 문서 수를 구분해야 한다.**  
   `case` 71,954건은 판례 71,954개가 아니라 판례 18,104개를 섹션별로 나눈
   청크 수다.

2. **섹션형 컬렉션의 실질 PK에는 `doc_type`이 필요하다.**  
   `case`, `constitutional_decc`, `legislation_expc`, `admin_decc`는
   섹션마다 `chunk_seq`가 다시 시작할 수 있으므로 `(doc_id, chunk_seq)`만으로
   유일성을 판단하면 안 된다.

3. **현재 `chunk_id` 인덱스는 unique가 아니다.**  
   애플리케이션 upsert 조건이 중복 방지의 주 책임을 가진다. 향후 unique 전환
   전에는 기존 중복 여부와 과거 ID 형식을 먼저 점검해야 한다.

4. **행정심판재결례는 기관 코드 필터가 아니라 키워드 필터다.**  
   노동 분야 키워드 목록의 변경에 따라 포함 범위가 달라질 수 있다.

5. **법령 수집 범위는 고용노동부만이 아니다.**  
   현재 `law` 스크래퍼는 개인정보보호위원회 법령도 함께 수집한다.

6. **상세 URL처럼 보이지만 식별용인 경우가 있다.**  
   `decision`은 별도 상세 본문이 없어 목록의 제목·날짜가 실제 저장 데이터다.

7. **중앙노동위원회 3개 유형은 같은 목록 API를 쓰지만 서로 다른 분류다.**  
   `judgment`는 `jgmtDcsnSeCd=65`, `mediation_case`는 `66`,
   `decision`은 `67`이다. 운영 DB에서 제목을 정규화해 비교한 결과 세 컬렉션
   사이에 동일 제목은 없었다. 다만 `mediation_case`와 `judgment`는
   `jgmtSn` 숫자 75개가 겹친다. 예를 들어 `doc_id="1"`은 두 컬렉션에 모두
   있지만 제목과 내용이 다른 별도 문서다. 따라서 컬렉션을 합쳐 조회하거나
   전역 인용 ID를 만들 때는 `doc_id`만 사용하지 말고
   `(collection, doc_id)` 또는 `chunk_id`를 사용해야 한다.

## 7. 관련 코드 위치

- 컬렉션 설정: `scripts/core/config.py`
- 일일 증분 실행과 필터: `crawl.py`, `run_daily.sh`
- 청크 ID 규칙: `scripts/core/identifiers.py`
- 변경 감지 저장소: `scripts/core/database/unified_repository.py`
- 공통 provenance: `scripts/core/database/source_versioning.py`
- 개별 목록·상세 수집:
  `scripts/<collection>/logic/list_scraper.py`,
  `scripts/<collection>/logic/scraper.py`
