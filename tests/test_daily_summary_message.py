import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.notify import daily_summary
from scripts.notify.alert_format import CHANNEL_ENV, format_alert, resolve_webhook
from scripts.notify.daily_summary import build_message


def ok(success=0, no_change=0, skipped=0, total=10):
    return {"status": "success", "total_urls": total, "total_success": success,
            "total_no_change": no_change, "total_failed": 0, "skipped": skipped}


SUCCESS = {"law": ok(skipped=165, total=165), "case": ok(success=3, skipped=29_997, total=30_000)}
# law.go.kr 404 로 목록조차 못 모은 밤 (2026-08-22 재현)
LISTING_FAILURE = {"law": {"status": "failed", "total_urls": 0, "total_success": 0, "total_failed": 0},
                   "decision": ok(skipped=511, total=511)}


def message(scrapers, **kw):
    kw.setdefault("duration_min", 19)
    kw.setdefault("attempts", 1)
    kw.setdefault("date_str", "2026-10-28 00:00")
    return build_message(scrapers, **kw)


class BuildMessageTest(unittest.TestCase):
    def test_success_is_info_report_in_common_format(self):
        lines = message(SUCCESS).split("\n")
        self.assertEqual(lines[0], "[INFO] 보고 · roeum-crawler(daily)")
        self.assertIn("전체 업데이트 3건", lines[1])
        self.assertEqual(lines[2], "- 환경: prod")
        self.assertTrue(lines[4].startswith("- 검색: 맥미니"))
        text = "\n".join(lines)
        self.assertIn("✅ 판례: 3건 업데이트", text)
        self.assertIn("⬜ 행정규칙: 미실행", text)
        self.assertNotIn("재시도", text)

    def test_partial_failure_on_successful_scraper_is_error(self):
        # status 가 success 여도 문서 실패가 있으면 숨기지 않는다(옛 로그 파싱은 ✅ 로 보여 줬다).
        scrapers = dict(SUCCESS, case=dict(ok(success=2, total=10), total_failed=4))
        msg = message(scrapers)
        self.assertTrue(msg.startswith("[ERROR] 데이터 수집 ·"))
        self.assertIn("판례 일부 문서 실패", msg.split("\n")[1])
        self.assertIn("❌ 판례: 실패 4건", msg)

    def test_listing_failure_is_warn_with_external_source_wording(self):
        msg = message(LISTING_FAILURE, attempts=3, exit_code=2)
        self.assertTrue(msg.startswith("[WARN] 데이터 수집 · roeum-crawler(daily)"))
        self.assertIn("법령 미수집", msg)
        self.assertIn("2회 재시도", msg)
        self.assertIn("데이터 유실 아님", msg)

    def test_crash_or_missing_result_is_error(self):
        msg = message({}, missing_attempt=True, exit_code=1)
        self.assertTrue(msg.startswith("[ERROR] 데이터 수집 ·"))
        self.assertIn("크롤러 비정상 종료(코드 1)", msg)

    def test_refresh_failure_is_error(self):
        msg = message(SUCCESS, refresh_exit_code=3)
        self.assertTrue(msg.startswith("[ERROR] 데이터 수집 ·"))
        self.assertIn("버전 재계산 실패(코드 3)", msg)

    def test_log_location_is_configurable_for_ecs(self):
        msg = message(SUCCESS, log_location="CloudWatch 로그 그룹 /ecs/roeum-crawler")
        self.assertIn("- 검색: CloudWatch 로그 그룹 /ecs/roeum-crawler", msg)

    def test_message_within_discord_limit(self):
        self.assertLessEqual(len(message(SUCCESS, attempts=3)), 2000)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.result = self.tmp / "attempt_1.json"
        self.result.write_text(json.dumps({"exit_code": 0, "scrapers": SUCCESS}))

    def run_main(self, env, send=None):
        out = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("scripts.notify.daily_summary.send_discord", send or mock.Mock()) as sent, \
             contextlib.redirect_stdout(out):
            daily_summary.main(["--attempt-json", str(self.result), "--date", "d"])
        return out.getvalue(), sent

    def test_reads_result_json_and_sends_to_data_collect_channel(self):
        out, sent = self.run_main({"DISCORD_WEBHOOK": "https://example.invalid/old",
                                   "DISCORD_WEBHOOK_DATA_COLLECT": "https://example.invalid/data"})
        url, msg = sent.call_args.args
        self.assertEqual(url, "https://example.invalid/data")
        self.assertIn("전체 업데이트 3건", msg)
        self.assertIn("전송 완료", out)

    def test_skips_without_webhook(self):
        out, sent = self.run_main({})
        sent.assert_not_called()
        self.assertIn("웹훅 환경변수 없음", out)

    def test_send_failure_does_not_print_webhook(self):
        url = "https://example.invalid/SECRET-TOKEN"
        out, _ = self.run_main({"DISCORD_WEBHOOK": url}, send=mock.Mock(side_effect=RuntimeError(url)))
        self.assertIn("전송 실패: RuntimeError", out)
        self.assertNotIn("SECRET-TOKEN", out)


class AlertFormatTest(unittest.TestCase):
    def test_format_shape_and_detail_limit(self):
        txt = format_alert("ERROR", "데이터 수집", "roeum-crawler(daily)", "요약\n두줄", "prod",
                           "/logs/a.log", "맥미니 로그", [f"d{i}" for i in range(20)], max_detail_lines=3)
        lines = txt.split("\n")
        self.assertEqual(lines[0], "[ERROR] 데이터 수집 · roeum-crawler(daily)")
        self.assertEqual(lines[1], "요약 두줄")
        self.assertIn("… 외 17줄", txt)
        self.assertNotIn("d3", txt)

    def test_rejects_unknown_severity_and_kind(self):
        with self.assertRaises(ValueError):
            format_alert("FATAL", "배치", "t", "s", "prod", "i", "w")
        with self.assertRaises(ValueError):
            format_alert("INFO", "기타", "t", "s", "prod", "i", "w")

    def test_channel_env_names_defined_for_four_channels(self):
        self.assertEqual(set(CHANNEL_ENV), {"server_error", "batch", "deploy", "data_collect"})

    def test_webhook_falls_back_to_existing_variable(self):
        env = {"DISCORD_WEBHOOK": "https://example.invalid/old"}
        self.assertEqual(resolve_webhook("data_collect", environ=env), "https://example.invalid/old")
        env["DISCORD_WEBHOOK_DATA_COLLECT"] = "https://example.invalid/new"
        self.assertEqual(resolve_webhook("data_collect", environ=env), "https://example.invalid/new")
        self.assertEqual(resolve_webhook("batch", environ=env), "https://example.invalid/old")


if __name__ == "__main__":
    unittest.main()
