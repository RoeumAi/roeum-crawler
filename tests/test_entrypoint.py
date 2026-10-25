"""docker/entrypoint.sh: 인자가 있으면 그 명령만 실행하고 일일 크롤링은 돌리지 않는다."""
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh"


def test_args_run_one_off_command_and_keep_exit_code():
    result = subprocess.run(["bash", str(ENTRYPOINT), "bash", "-c", "echo one-off; exit 3"],
                            capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "BATCH_REPORT_JOB": "legaldata-law-crawl-daily"})
    assert result.stdout.strip() == "one-off"
    assert result.returncode == 3
