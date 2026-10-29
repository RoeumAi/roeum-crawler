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
    return build_message(scrapers, **kw)["embeds"][0]


# 10-29 00:00 실행 재현: 별표 파싱 중 추출 서버가 재시작해 행정규칙 50건·법령 2건 실패
OCT29 = {
    "law": dict(ok(success=57, no_change=0, skipped=100, total=160), total_failed=2,
                failure_reasons={"국가법령 Open API 일시 오류": 2}),
    "adrule": dict(ok(success=105, skipped=24_000, total=24_155), total_failed=50,
                   failure_reasons={"문서 추출 서버(chat_generation) 연결 실패": 49, "국가법령 Open API 일시 오류": 1}),
    "case": ok(no_change=1, skipped=17_273, total=17_274),
    "constitutional_decc": ok(no_change=6, skipped=198, total=204),
}


class BuildMessageTest(unittest.TestCase):
    def test_success_is_one_embed_with_status_title_and_lines(self):
        embed = message(SUCCESS)
        self.assertEqual(embed["title"], "✅ 법률 자료 일일 수집 — 성공")
        lines = embed["description"].split("\n")
        self.assertEqual(lines[0], "🕛 2026-10-28 00:00 실행 · 19분 소요")
        self.assertEqual(lines[1], "")
        self.assertEqual(lines[2], "✅ 판례: 3건 업데이트 (변경없음 0 · 건너뜀 29,997)")  # 업데이트가 변경 없음보다 먼저
        self.assertEqual(lines[3], "✅ 법령: 변경 없음 (건너뜀 165)")
        self.assertIn("⬜ 행정규칙: 미실행", lines)
        self.assertEqual(lines[-1], "🔢 전체 업데이트 3건 · 변경없음 0건")
        self.assertNotIn("실패 원인", embed["description"])
        self.assertEqual(embed["footer"]["text"], "[INFO] 데이터 수집 · roeum-crawler(daily) · 환경 prod")

    def test_failures_come_first_with_reasons_and_log_location(self):
        embed = message(OCT29, duration_min=22, log_location="CloudWatch /ecs/roeum-crawler")
        self.assertEqual(embed["title"], "❌ 법률 자료 일일 수집 — 일부 실패")
        lines = embed["description"].split("\n")
        self.assertEqual(lines[2:6], [
            "❌ 법령: 실패 2건 (업데이트 57 · 변경없음 0)",
            "❌ 행정규칙: 실패 50건 (업데이트 105 · 변경없음 0)",
            "✅ 판례: 변경 없음 (재확인 1 · 건너뜀 17,273)",
            "✅ 헌재결정례: 변경 없음 (재확인 6 · 건너뜀 198)",
        ])
        text = embed["description"]
        self.assertIn("**실패 원인**\n• 문서 추출 서버(chat_generation) 연결 실패 49건\n• 국가법령 Open API 일시 오류 3건", text)
        self.assertIn("다음 실행에서 자동으로 다시 수집", text)
        self.assertIn("로그: CloudWatch /ecs/roeum-crawler", text)
        self.assertTrue(text.endswith("🔢 전체 업데이트 162건 · 변경없음 7건"))
        self.assertTrue(embed["footer"]["text"].startswith("[ERROR] 데이터 수집"))

    def test_failure_without_known_reason_is_counted_as_other(self):
        # status 가 success 여도 문서 실패가 있으면 숨기지 않는다.
        scrapers = dict(SUCCESS, case=dict(ok(success=2, total=10), total_failed=4))
        embed = message(scrapers)
        self.assertEqual(embed["title"], "❌ 법률 자료 일일 수집 — 일부 실패")
        self.assertIn("❌ 판례: 실패 4건", embed["description"])
        self.assertIn("• 기타 저장 실패 4건", embed["description"])

    def test_listing_failure_is_warn_with_external_source_wording(self):
        embed = message(LISTING_FAILURE, attempts=3, exit_code=2)
        self.assertEqual(embed["title"], "⚠️ 법률 자료 일일 수집 — 수집원 일시 오류")
        text = embed["description"]
        self.assertIn("수집원 오류로 2회 재시도", text)
        self.assertIn("법령 목록 미수집 — 데이터 유실 아님", text)
        self.assertTrue(embed["footer"]["text"].startswith("[WARN]"))

    def test_crash_or_missing_result_is_error(self):
        embed = message({}, missing_attempt=True, exit_code=1)
        self.assertEqual(embed["title"], "🚨 법률 자료 일일 수집 — 실패")
        self.assertIn("• 크롤러 비정상 종료(코드 1)", embed["description"])

    def test_refresh_failure_is_error(self):
        embed = message(SUCCESS, refresh_exit_code=3)
        self.assertIn("버전 재계산 실패(코드 3)", embed["description"])
        self.assertTrue(embed["footer"]["text"].startswith("[ERROR]"))

    def test_payload_is_valid_discord_embed_within_limits(self):
        payload = build_message(OCT29, duration_min=22, attempts=3, date_str="d")
        self.assertEqual(set(payload), {"embeds"})
        embed = payload["embeds"][0]
        self.assertLessEqual(len(embed["title"]), 256)
        self.assertLessEqual(len(embed["description"]), 4096)
        self.assertLessEqual(len(embed["footer"]["text"]), 2048)
        self.assertIsInstance(embed["color"], int)
        json.dumps(payload)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.result = self.tmp / "attempt_1.json"
        self.result.write_text(json.dumps({"exit_code": 0, "scrapers": SUCCESS}))

    def run_main(self, env, send=None):
        out = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("scripts.notify.daily_summary.send_discord_payload", send or mock.Mock()) as sent, \
             contextlib.redirect_stdout(out):
            daily_summary.main(["--attempt-json", str(self.result), "--date", "d"])
        return out.getvalue(), sent

    def test_reads_result_json_and_sends_to_data_collect_channel(self):
        out, sent = self.run_main({"DISCORD_WEBHOOK": "https://example.invalid/old",
                                   "DISCORD_WEBHOOK_DATA_COLLECT": "https://example.invalid/data"})
        url, payload = sent.call_args.args
        self.assertEqual(url, "https://example.invalid/data")
        self.assertIn("전체 업데이트 3건", payload["embeds"][0]["description"])
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
