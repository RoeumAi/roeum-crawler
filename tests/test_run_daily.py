"""run_daily.sh 전체 흐름: 재시도 범위, 종료코드, 마지막 줄 BATCH_RESULT, Discord 일일 요약 전송.

crawl.py·재계산 스크립트를 가짜로 바꾼 임시 저장소에서 실행한다(외부 접속·DB 없음).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FAKE_CRAWL = textwrap.dedent('''
    import json, os, sys
    from pathlib import Path
    args = sys.argv[1:]
    out = args[args.index("--result-json") + 1]
    scrapers = args[args.index("--scraper") + 1:] if "--scraper" in args else ["law", "case"]
    log = Path(os.environ["FAKE_CALLS"])
    log.write_text(log.read_text() + " ".join(scrapers) + "\\n" if log.exists() else " ".join(scrapers) + "\\n")
    attempt = len(log.read_text().splitlines())
    plan = json.loads(os.environ["FAKE_PLAN"])[attempt - 1]
    if plan == "crash":
        sys.exit(1)
    results = {name: plan[name] for name in scrapers}
    code = 2 if any(r["status"] == "failed" and not r.get("total_urls") for r in results.values()) else 0
    Path(out).write_text(json.dumps({"exit_code": code, "scrapers": results}))
    print("fake crawl", scrapers)
    sys.exit(code)
''')

OK = {"status": "success", "total_urls": 10, "total_success": 1}
LISTING_FAIL = {"status": "failed", "total_urls": 0, "failed_targets": ["law:listing"]}


class RunDailyTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        shutil.copy(REPO / "run_daily.sh", self.dir / "run_daily.sh")
        (self.dir / "scripts/report").mkdir(parents=True)
        shutil.copy(REPO / "scripts/report/batch_result.py", self.dir / "scripts/report/batch_result.py")
        # 요약 스크립트는 실제 파일을 쓴다(인자 계약이 어긋나면 이 테스트가 잡는다).
        shutil.copytree(REPO / "scripts/notify", self.dir / "scripts/notify",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.dir / "scripts/core").mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "scripts/core/config.py", self.dir / "scripts/core/config.py")
        for pkg in ("scripts", "scripts/core", "scripts/report"):
            (self.dir / pkg / "__init__.py").touch()
        (self.dir / "scripts/core/flows").mkdir(parents=True, exist_ok=True)
        (self.dir / "crawl.py").write_text(FAKE_CRAWL)
        self.refresh = self.dir / "scripts/core/flows/refresh_current_status_flow.py"
        self.refresh.write_text("print('refresh ok')\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_daily(self, plan, log_stdout="0", **extra_env):
        extra_env.setdefault("CRAWLER_DISCORD_SUMMARY", "0")
        env = dict(os.environ, **extra_env, PYTHON=sys.executable, CRAWLER_DIR=str(self.dir),
                   CRAWLER_LOCKFILE=str(self.dir / "lock"),
                   CRAWLER_LOG_STDOUT=log_stdout, RETRY_WAIT_SECONDS="0",
                   FAKE_PLAN=json.dumps(plan), FAKE_CALLS=str(self.dir / "calls"))
        proc = subprocess.run(["bash", str(self.dir / "run_daily.sh")], env=env,
                              capture_output=True, text=True, timeout=60)
        last = proc.stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("BATCH_RESULT "), proc.stdout + proc.stderr)
        calls = (self.dir / "calls").read_text().splitlines() if (self.dir / "calls").exists() else []
        return proc.returncode, json.loads(last.removeprefix("BATCH_RESULT ")), calls, proc.stdout

    def test_success_prints_only_batch_result_on_stdout(self):
        code, result, calls, stdout = self.run_daily([{"law": OK, "case": OK}])

        self.assertEqual(code, 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(calls, ["law case"])
        self.assertEqual(len(stdout.strip().splitlines()), 1)

    def test_retry_reruns_only_failed_scraper(self):
        code, result, calls, _ = self.run_daily([{"law": LISTING_FAIL, "case": OK}, {"law": OK}])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["law case", "law"])
        self.assertEqual(result["processed"], 2)
        self.assertIn("시도 2회", result["summary"])

    def test_listing_failure_after_all_attempts_exits_2(self):
        plan = [{"law": LISTING_FAIL, "case": OK}] + [{"law": LISTING_FAIL}] * 2
        code, result, calls, _ = self.run_daily(plan)

        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["failedTargets"], ["law:listing"])

    def test_crash_and_refresh_failure_exit_1(self):
        self.refresh.write_text("import sys; sys.exit(1)\n")
        code, result, _, _ = self.run_daily(["crash"])

        self.assertEqual(code, 1)
        self.assertEqual(result["failedTargets"], ["crawl:run", "refresh:current-status"])

    def test_log_stdout_mode_keeps_batch_result_last(self):
        code, _, _, stdout = self.run_daily([{"law": OK, "case": OK}], log_stdout="1")

        self.assertEqual(code, 0)
        self.assertIn("fake crawl", stdout)

    def test_resource_line_stays_before_batch_result(self):
        # 컨테이너(GNU time)에서는 RESOURCE 줄이 로그 안에 먼저 나오고 마지막 줄은 BATCH_RESULT.
        code, _, _, stdout = self.run_daily([{"law": OK, "case": OK}], log_stdout="1",
                                            CRAWLER_RESOURCE_LOG="1")

        self.assertEqual(code, 0)
        gnu_time = subprocess.run(["/usr/bin/time", "--version"], capture_output=True).returncode == 0 \
            if Path("/usr/bin/time").exists() else False
        if gnu_time:
            self.assertIn("RESOURCE max_rss_kb=", stdout)

    def test_daily_summary_is_posted_to_discord_webhook(self):
        received = []

        class Hook(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Hook)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        hook = f"http://127.0.0.1:{server.server_port}/webhook"
        partial = {"status": "success", "total_urls": 10, "total_success": 2, "total_failed": 1,
                   "failed_targets": ["case:doc-1"]}

        code, result, _, stdout = self.run_daily([{"law": OK, "case": partial}], log_stdout="1",
                                                 CRAWLER_DISCORD_SUMMARY="1",
                                                 DISCORD_WEBHOOK=hook, DISCORD_WEBHOOK_DATA_COLLECT="")

        self.assertEqual(code, 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(received), 1, "일일 요약이 웹훅으로 한 번 나가야 한다")
        embed = received[0]["embeds"][0]
        self.assertEqual(embed["title"], "❌ 법률 자료 일일 수집 — 일부 실패")
        self.assertIn("❌ 판례: 실패 1건", embed["description"])
        self.assertIn("✅ 법령: 1건 업데이트", embed["description"])
        self.assertTrue(embed["footer"]["text"].startswith("[ERROR] 데이터 수집 · roeum-crawler(daily)"))
        # 컨테이너(CRAWLER_LOG_STDOUT=1)에서도 전송 결과가 로그(CloudWatch)에 남는다. 마지막 줄은 여전히 BATCH_RESULT.
        self.assertIn("Discord 알림 전송 완료", stdout)


if __name__ == "__main__":
    unittest.main()
