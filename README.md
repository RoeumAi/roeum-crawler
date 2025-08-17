# roeum-crawler
crawler for roeumAi


## 법령 파이프라인 통합 실행 (run_law_scraper.sh)
option : 고용노동부 1492000 / 보건복지부 1352000, 법무부 1270000
option : 페이지 수 1,2,3 혹은 전체는 null

```Bash
 ./bin/run_law_scraper.sh 1492000 2
  ```

- 모든 작업이 끝난 후 data/final 폴더 내 all_documents.jsonl과 all_chuncks.jsonl 파일 생성
- logs/ 폴더 내 scraper.log 와 error.log에 로그 생성

### url 유효성 검사기 단독 실행 (url_checker.py)
```Bash
 python scripts/law/url_checker.py "https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd=1492000"
  ```

### 목록 스크래퍼 단독 실행 (list_scraper.py)
```Bash
 python scripts/run_list_scraper.py "https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd=1492000" -o data/raw/urls.jsonl
 option: -p 1
 option: -o filename
  ```

### url 상세 페이지 스크래퍼 단독 실해 (scraper.py)
- 파일 저장 위치 : /data/raw
```Bash
 python scripts/run_scraper.py "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=232959&efYd=20220616" -o "가사근로자법_테스트"
  ```

### jsonl 파일 csv로 변환 (notion 공유를 위함)
```Bash
 # documents 파일 변환 
 python scripts/utils/jsonl_to_csv.py -i "data/final/1492000/all_documents.jsonl" -o "data/final/1492000/for_notion_documents.csv"
 # chunks 파일 변환
 python scripts/utils/jsonl_to_csv.py -i "data/final/1492000/all_chunks.jsonl" -o "data/final/1492000/for_notion_chunks.csv"
  ```

