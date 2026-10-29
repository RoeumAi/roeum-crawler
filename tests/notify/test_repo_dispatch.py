"""크롤링 뒤 b2c 저장소에 법률 문서 공개 페이지 갱신을 요청한다. 실패해도 크롤링 결과에는 영향이 없어야 한다."""
import json
from unittest import mock

from scripts.notify import repo_dispatch

TOKEN_ENV = "LEGALDB_DISPATCH_TOKEN"
FAKE_TOKEN = "fake-token-for-test"


def _response(status):
    resp = mock.MagicMock()
    resp.status = status
    resp.__enter__.return_value = resp
    return resp


def test_missing_token_returns_false_without_request(monkeypatch, capsys):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    with mock.patch.object(repo_dispatch.urllib.request, "urlopen") as urlopen:
        assert repo_dispatch.dispatch_legaldb_changed() is False
    urlopen.assert_not_called()
    assert capsys.readouterr().out.strip().count("\n") == 0


def test_blank_token_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "  ")
    with mock.patch.object(repo_dispatch.urllib.request, "urlopen") as urlopen:
        assert repo_dispatch.dispatch_legaldb_changed() is False
    urlopen.assert_not_called()


def test_success_204_returns_true_and_sends_expected_request(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, FAKE_TOKEN)
    with mock.patch.object(repo_dispatch.urllib.request, "urlopen", return_value=_response(204)) as urlopen:
        assert repo_dispatch.dispatch_legaldb_changed() is True
    req = urlopen.call_args.args[0]
    assert req.full_url == "https://api.github.com/repos/RoeumAi/roeum-web-b2c/dispatches"
    assert req.get_method() == "POST"
    assert json.loads(req.data) == {"event_type": "legaldb-changed"}
    headers = {k.lower(): v for k, v in req.header_items()}
    assert headers["authorization"] == f"Bearer {FAKE_TOKEN}"
    assert headers["x-github-api-version"] == "2022-11-28"


def test_success_200_returns_true(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, FAKE_TOKEN)
    with mock.patch.object(repo_dispatch.urllib.request, "urlopen", return_value=_response(200)):
        assert repo_dispatch.dispatch_legaldb_changed() is True


def test_exception_is_swallowed_and_token_not_logged(monkeypatch, capsys):
    monkeypatch.setenv(TOKEN_ENV, FAKE_TOKEN)
    with mock.patch.object(repo_dispatch.urllib.request, "urlopen", side_effect=OSError(f"boom {FAKE_TOKEN}")):
        assert repo_dispatch.dispatch_legaldb_changed() is False
    out = capsys.readouterr()
    assert FAKE_TOKEN not in out.out + out.err


def test_main_never_fails(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, FAKE_TOKEN)
    with mock.patch.object(repo_dispatch.urllib.request, "urlopen", side_effect=RuntimeError("x")):
        assert repo_dispatch.main() == 0
