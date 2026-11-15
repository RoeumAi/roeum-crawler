import unittest
from unittest.mock import patch, MagicMock

from scripts.core.lawgokr_stable_id import fetch_stable_law_id, fetch_stable_adrule_id


class FetchStableLawIdTest(unittest.TestCase):
    @patch("scripts.core.lawgokr_openapi.requests.get")
    def test_returns_law_id_on_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"법령": {"기본정보": {"법령ID": "001872"}}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = fetch_stable_law_id("265959")

        self.assertEqual(result, "001872")
        mock_get.assert_called_once()
        called_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(called_params["target"], "law")
        self.assertEqual(called_params["MST"], "265959")

    @patch("scripts.core.lawgokr_openapi.requests.get")
    def test_returns_none_on_missing_field(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"법령": {"기본정보": {}}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        self.assertIsNone(fetch_stable_law_id("265959"))

    @patch("scripts.core.lawgokr_openapi.requests.get")
    def test_returns_none_on_request_exception(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        self.assertIsNone(fetch_stable_law_id("265959"))


class FetchStableAdruleIdTest(unittest.TestCase):
    @patch("scripts.core.lawgokr_openapi.requests.get")
    def test_returns_adrule_id_on_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"AdmRulService": {"행정규칙기본정보": {"행정규칙ID": "79793"}}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = fetch_stable_adrule_id("2100000229118")

        self.assertEqual(result, "79793")
        called_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(called_params["target"], "admrul")
        self.assertEqual(called_params["ID"], "2100000229118")

    @patch("scripts.core.lawgokr_openapi.requests.get")
    def test_returns_none_on_missing_field(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"AdmRulService": {"행정규칙기본정보": {}}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        self.assertIsNone(fetch_stable_adrule_id("2100000229118"))


class FetchRecordErrorTest(unittest.TestCase):
    @patch("scripts.core.run_report.time.sleep")
    @patch("scripts.core.lawgokr_openapi.requests.get")
    def test_not_found_notice_returns_none_without_retry(self, mock_get, sleep):
        mock_response = MagicMock()
        mock_response.json.return_value = {"Law": "일치하는 법령이 없습니다."}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        self.assertIsNone(fetch_stable_law_id("1"))
        self.assertEqual((mock_get.call_count, sleep.call_count), (1, 0))


if __name__ == "__main__":
    unittest.main()
