# AGENTS.md

Codex 등 에이전트용. 이 저장소 작업 지침의 정본은 **`README.md`** 다(명령·구조·결과 보고 규칙·수동 도구·환경 변수·저장소 위생). 먼저 읽는다.
수동 실행 도구: `docs/manual-tasks.md`.

## 에이전트 공통 규칙

- 크롤러는 **운영** MongoDB `original_db`에 쓴다. 실제 수집·저장 실행은 승인된 경로(일일 예약)로만 하고, 테스트는 가짜 로컬 주소(`MONGODB_URI=mongodb://127.0.0.1:1/x CRAWLER_ENV=local`)로 돌린다.
- 일일 실행 경로(`crawl.py`, `run_daily.sh`, `docker/entrypoint.sh`)를 바꾸면 main 병합 시 운영 ECS 에 배포된다. 동작이 바뀌는 변경은 PR 에서 따로 밝힌다.
- 정비·정리 PR 에서 수집·저장 로직은 건드리지 않는다.
