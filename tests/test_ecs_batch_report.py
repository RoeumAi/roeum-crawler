"""ECS 예약 실행이 배치 관리 시스템에 시작·종료를 보고하도록 묶여 있는지."""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_task_definition_wires_batch_report():
    container = json.loads((REPO / "ecs" / "task-definition.json").read_text())["containerDefinitions"][0]
    env = {e["name"]: e["value"] for e in container["environment"]}
    secrets = {s["name"]: s["valueFrom"] for s in container["secrets"]}
    assert env["BATCH_REPORT_JOB"] == "legaldata-law-crawl-daily"
    assert env["BATCH_REPORT_URL"] == "https://api.loumpartner.com"
    assert env["BATCH_REPORT_HOST"] == "ecs-fargate"
    assert secrets["BATCH_REPORT_KEY"].endswith(":parameter/app/java/INTERNAL_BATCH_REPORT_KEY")


def test_wrapper_is_in_repo_where_entrypoint_expects_it():
    assert (REPO / "ops" / "batch_report.py").exists()
    assert "/app/ops/batch_report.py" in (REPO / "docker" / "entrypoint.sh").read_text()
