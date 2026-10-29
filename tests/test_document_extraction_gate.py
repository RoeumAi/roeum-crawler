"""추출 호출 속도 제한: 하나씩·간격을 두고, 서버가 응답하지 않으면 잠시 보내지 않는다(10-29 chat_generation 재시작)."""
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

    def test_connection_error_pauses_further_calls(self):
        def down():
            raise requests.ConnectionError("HTTPConnectionPool(host='chat-generation.local')")
        with self.assertRaises(requests.ConnectionError):
            self.gate.call(down)
        sent = Mock()
        with self.assertRaises(extraction.ExtractServerUnavailable):
            self.gate.call(sent)
        sent.assert_not_called()  # 쉬는 동안은 서버에 보내지 않는다
        self.now[0] += extraction.EXTRACT_PAUSE_SECONDS + 1
        self.assertEqual(self.gate.call(lambda: {"ok": 1}), {"ok": 1})

    def test_server_error_pauses_but_client_error_does_not(self):
        def status(code):
            def fail():
                raise requests.HTTPError(str(code), response=Mock(status_code=code))
            return fail
        with self.assertRaises(requests.HTTPError):
            self.gate.call(status(400))
        self.assertEqual(self.gate.call(lambda: {"ok": 1}), {"ok": 1})
        with self.assertRaises(requests.HTTPError):
            self.gate.call(status(503))
        with self.assertRaises(extraction.ExtractServerUnavailable):
            self.gate.call(lambda: {"ok": 1})

    def test_call_extract_text_goes_through_gate(self):
        response = Mock(json=Mock(return_value={"is_success": True}), raise_for_status=Mock())
        with patch.object(extraction, "_gate", self.gate), \
             patch.object(extraction.http_requests, "post", return_value=response) as post:
            self.assertEqual(extraction.call_extract_text(b"%PDF", "a.pdf", base_url="http://x"), {"is_success": True})
            self.gate._paused_until = self.now[0] + 60
            with self.assertRaises(extraction.ExtractServerUnavailable):
                extraction.call_extract_text(b"%PDF", "b.pdf", base_url="http://x")
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
