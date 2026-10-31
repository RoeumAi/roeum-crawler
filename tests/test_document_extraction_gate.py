"""추출 호출 속도 제한: 하나씩·간격을 두고, 서버가 멈추면 되살아날 때까지 기다린다(10-29·10-30 chat_generation 재시작)."""
import unittest
from unittest.mock import Mock, patch

import requests

from scripts.utils import document_extraction as extraction


class ExtractGateTest(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.slept = []
        self.gate = extraction._ExtractGate(clock=lambda: self.now[0], sleep=self.slept.append)

    def test_calls_are_spaced(self):
        self.gate.call(lambda: {"ok": 1})
        self.now[0] += 0.5
        self.gate.call(lambda: {"ok": 2})
        self.assertEqual(self.slept, [extraction.EXTRACT_INTERVAL_SECONDS - 0.5])

    def _down(self):
        raise requests.ConnectionError("HTTPConnectionPool(host='chat-generation.local')")

    def test_waits_for_server_to_come_back_then_continues(self):
        # 10-30: 서버가 00:05 에 재시작해 1분 만에 돌아왔는데, 10분 멈춤 때문에 남은 별표 49건이 모두 실패했다.
        checks = iter([False, False, False, True])  # 실패 직후 확인 + 기다리며 두 번 더
        self.gate._healthy = lambda url: next(checks)
        with self.assertRaises(requests.ConnectionError):
            self.gate.call(self._down, "http://x")
        self.assertEqual(self.gate.call(lambda: {"ok": 1}, "http://x"), {"ok": 1})
        self.assertEqual(self.slept.count(extraction.EXTRACT_HEALTH_POLL_SECONDS), 2)

    def test_gives_up_when_server_does_not_come_back(self):
        def healthy(url):
            self.now[0] += extraction.EXTRACT_HEALTH_POLL_SECONDS
            return False
        self.gate._healthy = healthy
        with self.assertRaises(requests.ConnectionError):
            self.gate.call(self._down)
        sent = Mock()
        with self.assertRaises(extraction.ExtractServerUnavailable):
            self.gate.call(sent)
        with self.assertRaises(extraction.ExtractServerUnavailable):
            self.gate.call(sent)  # 다시 기다리지 않고 바로 건너뛴다
        sent.assert_not_called()

    def test_stops_after_repeated_server_failures_in_one_run(self):
        # 실패 직후에는 내려가 있고, 기다리면 돌아온다 — 재시작이 거듭되는 경우
        checks = iter([False, True] * extraction.EXTRACT_MAX_SERVER_FAILURES)
        self.gate._healthy = lambda url: next(checks)
        for _ in range(extraction.EXTRACT_MAX_SERVER_FAILURES):
            with self.assertRaises(requests.ConnectionError):
                self.gate.call(self._down)
        sent = Mock()
        with self.assertRaises(extraction.ExtractServerUnavailable):
            self.gate.call(sent)
        sent.assert_not_called()  # 사용자 채팅 서버를 거듭 재시작시키지 않는다

    def test_client_error_is_not_a_server_failure(self):
        def status(code):
            def fail():
                raise requests.HTTPError(str(code), response=Mock(status_code=code))
            return fail
        self.gate._healthy = Mock(return_value=True)
        for _ in range(extraction.EXTRACT_MAX_SERVER_FAILURES + 1):
            with self.assertRaises(requests.HTTPError):
                self.gate.call(status(400))
        self.assertEqual(self.gate.call(lambda: {"ok": 1}), {"ok": 1})
        self.gate._healthy.assert_not_called()
        with self.assertRaises(requests.HTTPError):
            self.gate.call(status(503))
        self.gate.call(lambda: {"ok": 1})
        self.gate._healthy.assert_called_once()

    def test_document_rejected_while_server_healthy_is_not_a_server_failure(self):
        # 10-31: 서버가 무거운 별표 PDF 작업만 끝내고 503 을 줬는데(본체·/health 정상), 두 건 만에 멈춤으로 세어
        # 남은 행정규칙 별표 49건을 보내지 않았다.
        def rejected():
            raise requests.HTTPError("503", response=Mock(status_code=503))
        self.gate._healthy = Mock(return_value=True)
        for _ in range(extraction.EXTRACT_MAX_SERVER_FAILURES + 1):
            with self.assertRaises(requests.HTTPError):
                self.gate.call(rejected, "http://x")
        self.assertEqual(self.gate.call(lambda: {"ok": 1}, "http://x"), {"ok": 1})
        self.assertNotIn(extraction.EXTRACT_HEALTH_POLL_SECONDS, self.slept)  # 기다리지도 않는다

    def test_call_extract_text_goes_through_gate(self):
        response = Mock(json=Mock(return_value={"is_success": True}), raise_for_status=Mock())
        with patch.object(extraction, "_gate", self.gate), \
             patch.object(extraction.http_requests, "post", return_value=response) as post:
            self.assertEqual(extraction.call_extract_text(b"%PDF", "a.pdf", base_url="http://x"), {"is_success": True})
            self.gate._given_up = "멈춤"
            with self.assertRaises(extraction.ExtractServerUnavailable):
                extraction.call_extract_text(b"%PDF", "b.pdf", base_url="http://x")
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
