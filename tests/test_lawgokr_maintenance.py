import unittest
from types import SimpleNamespace

from scripts.core import lawgokr_maintenance as m

# 2026-11-14 law.go.kr 점검 안내 페이지 머리 부분(실제 마크업 그대로)
SORRY_HTML = """<html><head><title>시스템 점검</title></head><body>
<div class="popup-header alert">시스템 점검
    <span>시스템 작업(중단) 시간 :<span style="font-weight:700;margin-left:10px;">11월 13일(금) 21:00 ~ 11월 14일(토) 21:00</span></span>
</div></body></html>"""
SORRY_URL = "https://www.law.go.kr/sorry/sorry.html?target=law&type=XML"


def response(url, text):
    return SimpleNamespace(url=url, text=text)


class ParseTest(unittest.TestCase):
    def test_sorry_page_gives_window(self):
        self.assertEqual(m.parse_maintenance(SORRY_URL, SORRY_HTML),
                         "11월 13일(금) 21:00 ~ 11월 14일(토) 21:00")

    def test_sorry_url_without_readable_window_is_still_maintenance(self):
        self.assertEqual(m.parse_maintenance(SORRY_URL, "<html>점검</html>"), m.UNKNOWN_WINDOW)

    def test_maintenance_title_on_other_url_counts(self):
        self.assertEqual(m.parse_maintenance("https://www.law.go.kr/DRF/lawSearch.do", SORRY_HTML),
                         "11월 13일(금) 21:00 ~ 11월 14일(토) 21:00")

    def test_normal_response_is_not_maintenance(self):
        self.assertIsNone(m.parse_maintenance("https://www.law.go.kr/DRF/lawSearch.do",
                                              "<LawSearch><totalCnt>1</totalCnt></LawSearch>"))


class ProbeTest(unittest.TestCase):
    def test_probe_follows_redirect_to_sorry_page(self):
        calls = []

        def get(url, **kw):
            calls.append((url, kw))
            return response(SORRY_URL, SORRY_HTML)

        self.assertEqual(m.probe_maintenance(get=get), "11월 13일(금) 21:00 ~ 11월 14일(토) 21:00")
        self.assertTrue(calls[0][1]["allow_redirects"])
        self.assertIn("timeout", calls[0][1])

    def test_probe_normal_site_returns_none(self):
        self.assertIsNone(m.probe_maintenance(get=lambda url, **kw: response(url, "<LawSearch/>")))

    def test_probe_network_error_returns_none(self):
        def get(url, **kw):
            raise ConnectionError("down")

        self.assertIsNone(m.probe_maintenance(get=get))


if __name__ == "__main__":
    unittest.main()
