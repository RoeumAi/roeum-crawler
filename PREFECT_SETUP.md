# 🚀 Prefect 워커 풀 설정 및 실행 가이드

## 현재 상태

✅ **Work Pool 설정 완료**
- Pool Name: `default`
- Type: `process`
- Concurrency Limit: `None` (무제한)

---

## 📋 사용 시나리오별 실행 방법

### 시나리오 1: 개발 환경 (추천)

**터미널 1 - Prefect 서버 시작**
```bash
prefect server start
```

**터미널 2 - Prefect 워커 시작**
```bash
prefect worker start --pool default
```

**터미널 3 - 크롤링 실행**
```bash
python3 crawl.py --scraper law --pages 1
```

✅ 장점: 
- 실시간 로그 확인 가능
- 디버깅 용이
- 각 터미널에서 독립적으로 제어 가능

---

### 시나리오 2: 백그라운드 실행

**한 번에 실행:**
```bash
# Prefect 서버 + 워커를 백그라운드에서 시작
prefect server start > /tmp/prefect_server.log 2>&1 &
sleep 2
prefect worker start --pool default > /tmp/prefect_worker.log 2>&1 &

# 크롤링 실행
python3 crawl.py --scraper law --pages 1
```

✅ 장점:
- 한 번의 명령어로 실행
- 터미널이 자유로움

---

### 시나리오 3: 프로덕션 (권장)

**Bash 스크립트 사용:**
```bash
chmod +x run_crawler.sh
./run_crawler.sh
```

**run_crawler.sh 내용:**
```bash
#!/bin/bash

# Prefect 서버 시작
prefect server start > /tmp/prefect_server.log 2>&1 &
SERVER_PID=$!
sleep 2

# Prefect 워커 시작
prefect worker start --pool default > /tmp/prefect_worker.log 2>&1 &
WORKER_PID=$!
sleep 2

# 크롤링 실행
python3 crawl.py --scraper law --pages 1

# 정리
kill $SERVER_PID $WORKER_PID
```

---

## 🔍 상태 확인

### 워커 상태 확인
```bash
prefect work-pool ls
```

### 실행 중인 프로세스 확인
```bash
ps aux | grep prefect
```

### 로그 확인
```bash
tail -f /tmp/prefect_server.log
tail -f /tmp/prefect_worker.log
```

---

## 🛑 프로세스 중지

### 개별 중지
```bash
# 워커 중지 (Ctrl+C)
# 서버 중지 (Ctrl+C)
```

### 전체 중지
```bash
pkill -f prefect
```

---

## 🔧 트러블슈팅

### 에러: "Port 4200 is already in use"
```bash
# 다른 포트 사용
prefect server start --port 4201
```

### 에러: "Cannot put items in a stopped service instance"
- **무시해도 됨** - Prefect 내부 이벤트 로깅 에러
- 실제 크롤링 기능에는 영향 없음
- 워커 풀이 제대로 설정되면 로그가 깔끔해짐

### 워커가 작업을 받지 않음
```bash
# 1. 워커가 running 상태인지 확인
prefect worker ls

# 2. Flow가 정확한 work pool을 사용하는지 확인
grep -n "work_pool_name" scripts/core/flows/unified_scraper_flow.py
```

---

## 📊 현재 Flow 설정

**파일:** `scripts/core/flows/unified_scraper_flow.py`

```python
@flow(name="unified-scraper", work_pool_name="default")
async def unified_scraper_flow(
    scraper_type: str,
    max_pages: Optional[int] = None,
    max_concurrent: int = 3
):
    ...
```

✅ Work pool name: `"default"` 로 설정됨
- 워커 풀이 지정되면 이벤트 에러 감소
- Flow 실행이 더 안정적

---

## 💡 권장 사항

### 개발 중
- 터미널 3개 분할 (서버, 워커, 크롤링)
- 각각의 로그를 실시간으로 모니터링
- Prefect UI에서 Flow 진행 상황 확인

### 프로덕션
- 스크립트 자동화 (`run_crawler.sh`)
- 로그 파일로 기록
- Systemd 또는 Supervisor로 자동 재시작 설정

### 성능 최적화
- Worker 동시성 조정: `--concurrency` 플래그 사용
- Task 타임아웃: Flow에 설정
- 재시도 정책: Task 데코레이터에 설정
