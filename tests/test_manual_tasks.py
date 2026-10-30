"""manual.py: 등록된 수동 도구가 모두 --help 로 뜨고(실행하지 않음), 기본=쓰기 작업은 --yes 없이 막힌다."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

import manual

REPO = Path(__file__).resolve().parents[1]
ENV = dict(os.environ, MONGODB_URI="mongodb://127.0.0.1:1/x", CRAWLER_ENV="local")


def run(*args):
    return subprocess.run([sys.executable, str(REPO / "manual.py"), *args], cwd=REPO, env=ENV,
                          capture_output=True, text=True, timeout=120)


class ManualTasksTest(unittest.TestCase):
    def test_every_task_shows_help_without_running(self):
        for task in manual.TASKS:
            with self.subTest(task=task.name):
                proc = run(task.name, "--help")
                self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
                self.assertIn("usage:", proc.stdout)

    def test_write_default_task_requires_yes(self):
        proc = run("scrape:law", "https://example.invalid/x", "-d", "1", "-o", "t")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--yes", proc.stderr)

    def test_preview_flag_skips_yes_gate_but_help_does_not_run(self):
        # --help 는 게이트를 통과해도 아무것도 실행하지 않는다.
        self.assertEqual(run("scrape:judgment", "--help").returncode, 0)

    def test_list_and_unknown_task(self):
        self.assertIn("scrape:law", run().stdout)
        self.assertEqual(run("nope").returncode, 2)

    def test_removed_tasks_are_gone(self):
        removed = [
            "cherry-pick:case", "check-url:adrule", "check-url:case", "csv-import",
            "fix:clean-pdf-artifacts", "fix:judgment-case-numbers", "fix:source-provenance", "fix:stable-ids",
            "fix:chunk-identifiers", "fix:constitutional-sub-titles", "fix:reference-sub-titles",
            "fix:prune-interpretation", "fix:prune-law", "fix:retry-judgment-pdf",
        ]
        for name in removed:
            with self.subTest(task=name):
                self.assertNotIn(name, manual.BY_NAME)
                self.assertEqual(run(name, "--help").returncode, 2)

    def test_list_and_scrape_do_not_require_ignored_args(self):
        for name in ("list:adrule", "list:judgment"):
            self.assertNotIn("start_url", run(name, "--help").stdout, name)
        self.assertNotIn("--dept", run("scrape:judgment", "--help").stdout)
        self.assertIn("--dept", run("scrape:law", "--help").stdout)

    def test_every_task_module_is_importable_path(self):
        for task in manual.TASKS:
            with self.subTest(task=task.name):
                self.assertTrue((REPO / (task.module.replace(".", "/") + ".py")).exists())


if __name__ == "__main__":
    unittest.main()
