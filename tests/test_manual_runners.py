"""범용 수동 러너: 종류별 표가 기존 러너와 같은 인자로 수집 함수를 부른다(네트워크·DB 없이)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import manual_runners


class ManualRunnersTest(unittest.TestCase):
    def test_list_judgment_writes_jsonl_without_start_url(self):
        calls = []

        async def fake_fetch(start_url, max_pages):
            calls.append((start_url, max_pages))
            return [{"url": "u1"}, {"url": "u2"}]

        with tempfile.TemporaryDirectory() as tmp, \
                patch("scripts.judgment.logic.list_scraper.fetch_urls", fake_fetch):
            out = Path(tmp) / "sub" / "j.jsonl"
            self.assertEqual(manual_runners.main(["list", "judgment", "-p", "2", "-o", str(out)]), 0)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows, [{"url": "u1"}, {"url": "u2"}])
        self.assertEqual(calls, [("", 2)])

    def test_list_case_passes_optional_start_url(self):
        calls = []

        async def fake_fetch(start_url, max_pages):
            calls.append(start_url)
            return []

        with tempfile.TemporaryDirectory() as tmp, \
                patch("scripts.case.logic.list_scraper.fetch_urls", fake_fetch):
            manual_runners.main(["list", "case", "https://x/?cptOfiCd=1492000", "-o", str(Path(tmp) / "c.jsonl")])
        self.assertEqual(calls, ["https://x/?cptOfiCd=1492000"])

    def test_scrape_keeps_each_kinds_save_options(self):
        seen = {}

        def factory(kind):
            async def fake(url, output_dir, name, **kwargs):
                seen[kind] = (url, Path(output_dir).parts[-3:], name, kwargs)
            return fake

        for kind in ("law", "adrule", "case", "judgment"):
            with patch(f"scripts.{kind}.logic.scraper.scrape_and_save", factory(kind)):
                argv = ["scrape", kind, "U", "-o", "n", "--no-db"]
                if kind != "judgment":
                    argv += ["-d", "1492000"]
                self.assertEqual(manual_runners.main(argv), 0)

        self.assertEqual(seen["law"][3], {"save_to_db": False})
        self.assertEqual(seen["adrule"][3], {"save_to_db": False, "save_jsonl": True, "dept_code": "1492000"})
        self.assertEqual(seen["case"][3], {"save_to_db": False, "process_chunks": True, "dept_code": "1492000"})
        self.assertEqual(seen["judgment"][3], {"save_to_db": False, "save_jsonl": True})
        self.assertEqual(seen["law"][1], ("raw", "law", "1492000"))
        self.assertEqual(seen["judgment"][1][-2:], ("raw", "judgment"))

    def test_unknown_kind_is_usage_error(self):
        self.assertEqual(manual_runners.main(["scrape", "nope"]), 2)
        self.assertEqual(manual_runners.main(["check-url", "case", "u"]), 2)


if __name__ == "__main__":
    unittest.main()
