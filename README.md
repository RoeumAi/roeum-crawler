# roeum-crawler
crawler for roeumAi


## 판례 파이프라인 통합 실행 (run_law_scraper.sh)
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

