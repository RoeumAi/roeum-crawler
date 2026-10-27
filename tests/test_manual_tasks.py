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
        proc = run("fix:prune-law")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--yes", proc.stderr)

    def test_list_and_unknown_task(self):
        self.assertIn("fix:prune-law", run().stdout)
        self.assertEqual(run("nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
