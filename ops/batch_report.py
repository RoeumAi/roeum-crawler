#!/usr/bin/env python3
# 출처: RoeumAi/roeum-manage-server tools/batch-report/batch_report.py (7e227bde) — 그 파일이 원본이다.
# 고칠 때는 원본을 고치고 이 파일에 그대로 복사한다. 키는 환경변수(BATCH_REPORT_KEY)로만 받는다(공개 저장소).
"""배치 관리 시스템 외부 작업 보고 래퍼 (표준 라이브러리만, Python 3.9+).

사용: batch_report.py <jobName> -- <명령> [인자...]

원래 명령을 그대로 실행하면서 시작·종료를 배치 관리 서버(/internal/batch/runs/**)에 보고한다.
- 출력은 그대로 흘려보내고(launchd·컨테이너 로그 경로 유지) 끝 8KB만 보관해 보고한다.
- 판정: 종료코드 != 0 → FAILED. 0이면 표준 출력의 마지막 `BATCH_RESULT {json}` 줄에서
  failed > 0 → PARTIAL, 아니면 SUCCESS. 줄이 없거나 깨졌으면 종료코드만으로 판정한다.
- 보고가 실패해도 작업 실행·종료코드에는 영향이 없다. 네트워크 오류·5xx 는 그 자리에서 몇 번 다시 보낸다
  (BATCH_REPORT_RETRY_DELAYS 초, 기본 5,15,45). 시작 보고를 끝내 못 했으면 종료 때 원래 시작 시각으로 시작·종료를 함께 보낸다.
- 실행 id(UUID)는 래퍼가 실행마다 한 번 정해 시작 보고에 싣는다. 서버가 저장했는데 응답만 끊겨 다시 보내도 서버는 같은 실행을
  돌려주므로 "실행 중" 기록이 하나 더 생기지 않는다(스풀에서 다시 보낼 때도 같은 id).
- 그래도 못 보낸 실행은 스풀에 남겼다가 다음 실행 때 원래 시각(startedAt·finishedAt)으로 다시 보낸다(서버가 7일 넘은 시각은
  거절하므로 버린다). ECS 처럼 실행마다 디스크가 새로 생기는 곳에서는 스풀이 남지 않으므로 위의 즉시 재시도가 주 경로다.

환경변수: BATCH_REPORT_URL(서버 기준 주소), BATCH_REPORT_KEY 또는 BATCH_REPORT_KEY_FILE,
선택 BATCH_REPORT_HOST(기본 hostname), BATCH_REPORT_SPOOL(기본 ~/.batch-report/spool),
BATCH_REPORT_RETRY_DELAYS(쉼표로 구분한 재시도 간격 초, 기본 5,15,45 — 0 이면 재시도 없음).
원본은 roeum-manage-server tools/batch-report/ 이며, 크롤러 이미지에는 이 파일을 복사해 쓴다.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

KEY_HEADER = "X-Internal-Report-Key"
TAIL_BYTES = 8192
MAX_TARGETS = 50
MAX_SUMMARY = 500
SPOOL_MAX_AGE = timedelta(days=7)
HTTP_TIMEOUT = 10
DEFAULT_RETRY_DELAYS = (5, 15, 45)


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def parse_batch_result(lines):
    """마지막 `BATCH_RESULT {json}` 줄을 dict로. 없거나 깨졌으면 None."""
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("BATCH_RESULT "):
            try:
                value = json.loads(line[len("BATCH_RESULT "):])
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


def decide_status(exit_code, result):
    if exit_code != 0:
        return "FAILED"
    if result and int(result.get("failed") or 0) > 0:
        return "PARTIAL"
    return "SUCCESS"


class TailBuffer:
    """끝 TAIL_BYTES 바이트와 마지막 줄들만 보관한다(큰 로그도 메모리를 쓰지 않는다)."""

    def __init__(self):
        self._chunks = deque()
        self._size = 0
        self._lock = threading.Lock()

    def add(self, data):
        with self._lock:
            self._chunks.append(data)
            self._size += len(data)
            while self._size - len(self._chunks[0]) >= TAIL_BYTES:
                self._size -= len(self._chunks.popleft())

    def text(self):
        with self._lock:
            raw = b"".join(self._chunks)[-TAIL_BYTES:]
        return raw.decode("utf-8", errors="ignore")


def _pump(src, sink, tail, stdout_lines=None):
    for raw in iter(src.readline, b""):
        tail.add(raw)
        if stdout_lines is not None:
            stdout_lines.append(raw.decode("utf-8", errors="ignore"))
        try:
            sink.write(raw.decode("utf-8", errors="replace"))
            sink.flush()
        except (OSError, ValueError):
            pass
    src.close()


def run_command(cmd, stdout, stderr):
    """명령을 실행하며 출력을 흘려보낸다. (종료코드, 마지막 stdout 줄들, 끝 8KB)."""
    tail = TailBuffer()
    last_lines = deque(maxlen=200)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        stderr.write("batch-report: 명령 실행 실패: %s\n" % e)
        return 127, [], str(e)
    t_out = threading.Thread(target=_pump, args=(proc.stdout, stdout, tail, last_lines))
    t_err = threading.Thread(target=_pump, args=(proc.stderr, stderr, tail))
    t_out.start()
    t_err.start()
    code = proc.wait()
    t_out.join()
    t_err.join()
    return code, list(last_lines), tail.text()


def _retryable(error):
    """다시 보내면 받을 수 있는 실패인가 — 네트워크 오류·시간 초과·5xx. 4xx 는 계약상 다시 보내도 받지 않는다."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500
    return isinstance(error, (urllib.error.URLError, OSError))


def parse_retry_delays(value):
    """'5,15,45' → (5.0, 15.0, 45.0). 비었으면 기본값, '0' 이면 재시도 없음, 깨졌으면 기본값."""
    if value is None or not str(value).strip():
        return DEFAULT_RETRY_DELAYS
    try:
        delays = tuple(float(v) for v in str(value).split(",") if v.strip())
    except ValueError:
        return DEFAULT_RETRY_DELAYS
    return tuple(d for d in delays if d > 0)


class Reporter:
    def __init__(self, url, key, host, spool_dir, opener=None, retry_delays=(), sleep=None):
        self.url = url.rstrip("/")
        self.key = key
        self.host = host
        self.spool_dir = spool_dir
        self.opener = opener or urllib.request.urlopen
        self.retry_delays = tuple(retry_delays)
        self.sleep = sleep or time.sleep

    def _post_once(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", KEY_HEADER: self.key})
        with self.opener(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, resp.read()

    def _post(self, path, body):
        """다시 보낼 수 있는 실패면 retry_delays 간격으로 다시 보내고, 끝내 실패하면 마지막 예외를 던진다."""
        for delay in self.retry_delays:
            try:
                return self._post_once(path, body)
            except Exception as e:  # noqa: BLE001 — 재시도 여부만 가리고 나머지는 그대로 던진다
                if not _retryable(e):
                    raise
                self.sleep(delay)
        return self._post_once(path, body)

    def start(self, job, started_at, run_id=None):
        """시작 보고. run_id 를 실으면 서버가 같은 id 의 실행을 다시 만들지 않는다(재전송에 안전)."""
        payload = {"jobName": job, "host": self.host, "startedAt": started_at}
        if run_id:
            payload["runId"] = run_id
        status, body = self._post("/internal/batch/runs/start", payload)
        return json.loads(body.decode("utf-8"))["runId"] if status in (200, 201) else None

    def finish(self, run_id, payload):
        """True = 서버가 받았거나(204) 이미 끝난 실행(409) — 더 보낼 필요 없음."""
        try:
            self._post("/internal/batch/runs/%s/finish" % run_id, payload)
            return True
        except urllib.error.HTTPError as e:
            return e.code == 409

    def spool(self, record):
        os.makedirs(self.spool_dir, exist_ok=True)
        path = os.path.join(self.spool_dir, "%s.json" % uuid.uuid4().hex)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)

    def flush_spool(self):
        """스풀에 남은 실행을 원래 시각으로 다시 보낸다. 7일 넘은 기록·서버가 거절한 기록은 버린다."""
        if not os.path.isdir(self.spool_dir):
            return
        for name in sorted(os.listdir(self.spool_dir)):
            path = os.path.join(self.spool_dir, name)
            try:
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
                started = datetime.fromisoformat(rec["startedAt"])
                if datetime.now(timezone.utc) - started > SPOOL_MAX_AGE:
                    os.remove(path)
                    continue
                run_id = rec.get("runId") or self.start(rec["jobName"], rec["startedAt"], rec.get("startId"))
                if run_id and self.finish(run_id, rec["finish"]):
                    os.remove(path)
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    os.remove(path)  # 계약상 다시 보내도 받지 않는다(이름 없음·시각 범위 밖 등)
            except (OSError, ValueError, KeyError):
                continue  # 네트워크 장애 등 — 다음 실행 때 다시


def _read_key(env):
    if env.get("BATCH_REPORT_KEY"):
        return env["BATCH_REPORT_KEY"].strip()
    path = env.get("BATCH_REPORT_KEY_FILE")
    if path:
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return None
    return None


def build_finish(code, result, excerpt, finished_at):
    payload = {"status": decide_status(code, result), "finishedAt": finished_at, "logExcerpt": excerpt}
    if result:
        if result.get("processed") is not None:
            payload["processedCount"] = int(result["processed"])
        if result.get("failed") is not None:
            payload["failedCount"] = int(result["failed"])
        targets = result.get("failedTargets") or []
        if isinstance(targets, list):
            payload["failedTargets"] = [str(t) for t in targets[:MAX_TARGETS]]
        if result.get("summary"):
            payload["summary"] = str(result["summary"])[:MAX_SUMMARY]
    if code != 0:
        payload["error"] = "exit code %d" % code
    return payload


def main(argv=None, env=None, opener=None, stdout=None, stderr=None, sleep=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    if len(argv) < 3 or argv[1] != "--":
        stderr.write("사용: batch_report.py <jobName> -- <명령> [인자...]\n")
        return 2
    job, cmd = argv[0], argv[2:]

    url, key = env.get("BATCH_REPORT_URL"), _read_key(env)
    reporter = None
    if url and key:
        reporter = Reporter(url, key, env.get("BATCH_REPORT_HOST") or socket.gethostname(),
                            os.path.expanduser(env.get("BATCH_REPORT_SPOOL") or "~/.batch-report/spool"), opener,
                            parse_retry_delays(env.get("BATCH_REPORT_RETRY_DELAYS")), sleep)
    else:
        stderr.write("batch-report: BATCH_REPORT_URL/키가 없어 보고 없이 실행만 한다\n")

    started_at = now_iso()
    start_id = str(uuid.uuid4())  # 이 실행의 id — 시작 보고를 몇 번 다시 보내도 같은 값
    run_id = None
    if reporter:
        try:
            reporter.flush_spool()
            run_id = reporter.start(job, started_at, start_id)
        except Exception as e:  # noqa: BLE001 — 보고 실패가 실행을 막으면 안 된다
            stderr.write("batch-report: 시작 보고 실패(%s) — 끝난 뒤 시작·종료를 함께 다시 보낸다\n" % e)

    code, last_lines, excerpt = run_command(cmd, stdout, stderr)
    finish = build_finish(code, parse_batch_result(last_lines), excerpt, now_iso())

    if reporter:
        sent = False
        try:
            if not run_id:
                # 시작 보고를 못 했으면 원래 시작 시각으로 지금 연다(스풀이 남지 않는 ECS 에서도 기록이 빠지지 않게).
                run_id = reporter.start(job, started_at, start_id)
            if run_id:
                sent = reporter.finish(run_id, finish)
        except Exception as e:  # noqa: BLE001
            stderr.write("batch-report: 종료 보고 실패(%s)\n" % e)
        if not sent:
            try:
                reporter.spool({"jobName": job, "runId": run_id, "startId": start_id, "startedAt": started_at,
                                "finish": finish})
                stderr.write("batch-report: 보고하지 못한 실행을 스풀에 남겼다(다음 실행 때 재전송)\n")
            except OSError as e:
                stderr.write("batch-report: 스풀 저장 실패(%s)\n" % e)
    return code


if __name__ == "__main__":
    sys.exit(main())
